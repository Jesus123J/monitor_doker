import os
import time
import urllib3
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user
)
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, IntegrityError

from models import db, User, MonitoredTarget, StatusLog
from monitor import docker_containers, passbolt_status, checkmk_status, db_status

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ["FLASK_SECRET_KEY"]

DB_HOST = os.environ["DB_HOST"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_NAME = os.environ["DB_NAME"]

app.config["SQLALCHEMY_DATABASE_URI"] = (
    f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}?charset=utf8mb4"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def _wait_for_db(retries: int = 30, delay: int = 2):
    """Espera a que la DB acepte conexiones."""
    for i in range(retries):
        try:
            with app.app_context():
                db.session.execute(text("SELECT 1"))
            return True
        except OperationalError:
            time.sleep(delay)
    return False


def _bootstrap():
    """Crea las tablas y el usuario admin de seed si la DB esta vacia."""
    with app.app_context():
        db.create_all()

        admin_user = os.environ.get("DASHBOARD_ADMIN_USER")
        admin_pwd = os.environ.get("DASHBOARD_ADMIN_PASSWORD")
        admin_email = os.environ.get("DASHBOARD_ADMIN_EMAIL", "admin@local")

        if admin_user and admin_pwd and User.query.count() == 0:
            u = User(username=admin_user, email=admin_email, is_admin=True)
            u.set_password(admin_pwd)
            db.session.add(u)
            try:
                db.session.commit()
                app.logger.info("Admin de seed creado: %s", admin_user)
            except IntegrityError:
                # Otro worker ya lo creo en paralelo, no es un error real.
                db.session.rollback()


@app.route("/")
@login_required
def index():
    containers, docker_err = docker_containers()

    pb_state, pb_detail = passbolt_status(os.environ.get("PASSBOLT_URL", ""))
    cmk_state, cmk_detail = checkmk_status(
        os.environ.get("CHECKMK_URL", ""),
        os.environ.get("CHECKMK_USER", ""),
        os.environ.get("CHECKMK_SECRET", ""),
    )
    db_state, db_detail = db_status(db.engine)

    services = [
        {"name": "Passbolt", "status": pb_state, "detail": pb_detail},
        {"name": "Checkmk",  "status": cmk_state, "detail": cmk_detail},
        {"name": "DB central (MariaDB)", "status": db_state, "detail": db_detail},
    ]

    return render_template(
        "dashboard.html",
        services=services,
        containers=containers,
        docker_err=docker_err,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("index"))
        flash("Credenciales invalidas", "danger")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if len(password) < 8:
            flash("El password debe tener al menos 8 caracteres", "warning")
            return render_template("register.html")

        if User.query.filter(
            (User.username == username) | (User.email == email)
        ).first():
            flash("Ese usuario o email ya existe", "warning")
            return render_template("register.html")

        u = User(username=username, email=email)
        u.set_password(password)
        # primer usuario = admin
        if User.query.count() == 0:
            u.is_admin = True
        db.session.add(u)
        db.session.commit()
        flash("Cuenta creada, ya puedes ingresar", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/healthz")
def healthz():
    return {"status": "ok"}, 200


if _wait_for_db():
    _bootstrap()
else:
    raise RuntimeError("DB central no respondio a tiempo")
