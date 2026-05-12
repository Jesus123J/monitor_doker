import os
import time
import urllib3
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for, flash, abort
)
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user
)
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, IntegrityError

from models import db, User, MonitoredTarget, StatusLog, AuditLog
from monitor import (
    docker_containers, passbolt_status, checkmk_status, db_status,
    container_logs, container_stats, container_inspect, container_action,
    find_problems, db_tables, mirror_status, db_overview,
)

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


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _audit(action: str, target: str, success: bool, detail: str = ""):
    entry = AuditLog(
        user_id=current_user.id,
        username=current_user.username,
        action=action,
        target=target,
        success=success,
        detail=detail[:1000],
    )
    db.session.add(entry)
    db.session.commit()


# ---------- Rutas principales ----------

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
    dbs_state, dbs_detail = db_status(db.engine)

    services = [
        {
            "name": "Passbolt",
            "status": pb_state,
            "detail": pb_detail,
            "url": "https://localhost:8443/",
        },
        {
            "name": "Checkmk",
            "status": cmk_state,
            "detail": cmk_detail,
            "url": "http://localhost:5050/monitor/",
        },
        {
            "name": "DB central (MariaDB)",
            "status": dbs_state,
            "detail": dbs_detail,
            "url": None,
        },
    ]

    problems, _ = find_problems()

    return render_template(
        "dashboard.html",
        services=services,
        containers=containers,
        docker_err=docker_err,
        problem_count=len(problems),
    )


@app.route("/container/<name>")
@login_required
def container_detail(name):
    info = container_inspect(name)
    if "error" in info:
        flash(f"No pude inspeccionar {name}: {info['error']}", "danger")
        return redirect(url_for("index"))
    stats = container_stats(name)
    logs = container_logs(name, lines=300)
    return render_template(
        "container_detail.html",
        info=info, stats=stats, logs=logs,
    )


@app.route("/container/<name>/action", methods=["POST"])
@admin_required
def container_act(name):
    action = request.form.get("action", "")
    ok, detail = container_action(name, action)
    _audit(action=action, target=name, success=ok, detail=detail)
    flash(
        f"{action} sobre {name}: {detail}",
        "success" if ok else "danger",
    )
    return redirect(url_for("container_detail", name=name))


@app.route("/problems")
@login_required
def problems():
    items, err = find_problems()
    return render_template("problems.html", problems=items, err=err)


@app.route("/schema")
@admin_required
def schema():
    tables, err = db_tables(db.engine)
    return render_template("schema.html", tables=tables, err=err)


@app.route("/users")
@admin_required
def users_list():
    users = User.query.order_by(User.id.asc()).all()
    return render_template("users.html", users=users)


@app.route("/users/<int:user_id>/toggle-admin", methods=["POST"])
@admin_required
def users_toggle_admin(user_id):
    u = User.query.get_or_404(user_id)

    # No permitir demoverse si seria el ultimo admin
    if u.is_admin and User.query.filter_by(is_admin=True).count() == 1:
        flash("No puedes demover al ultimo admin del sistema.", "warning")
        return redirect(url_for("users_list"))

    u.is_admin = not u.is_admin
    db.session.commit()
    _audit(action=("promote" if u.is_admin else "demote"),
           target=u.username, success=True,
           detail=f"is_admin={u.is_admin}")
    flash(f"{u.username} ahora es {'admin' if u.is_admin else 'usuario normal'}.", "success")
    return redirect(url_for("users_list"))


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def users_delete(user_id):
    u = User.query.get_or_404(user_id)

    if u.id == current_user.id:
        flash("No puedes borrar tu propia cuenta.", "warning")
        return redirect(url_for("users_list"))

    if u.is_admin and User.query.filter_by(is_admin=True).count() == 1:
        flash("No puedes borrar al ultimo admin.", "warning")
        return redirect(url_for("users_list"))

    username = u.username
    db.session.delete(u)
    db.session.commit()
    _audit(action="delete_user", target=username, success=True)
    flash(f"Usuario {username} eliminado.", "success")
    return redirect(url_for("users_list"))


@app.route("/db-central")
@admin_required
def db_central():
    overview = db_overview(
        db_host=os.environ.get("DB_HOST", "db-central"),
        db_user="root",
        db_password=os.environ.get("DB_ROOT_PASSWORD", ""),
    )
    return render_template("db_central.html", overview=overview)


@app.route("/mirror")
@login_required
def mirror():
    info = mirror_status(
        status_file=os.environ.get("MIRROR_STATUS_FILE", "/var/lib/db-sync/last_sync"),
        mirror_host=os.environ.get("MIRROR_HOST", "db-mirror"),
        mirror_user="root",
        mirror_password=os.environ.get("DB_ROOT_PASSWORD", ""),
    )
    return render_template("mirror.html", info=info)


@app.route("/audit")
@admin_required
def audit():
    page_size = 100
    rows = (AuditLog.query
            .order_by(AuditLog.created_at.desc())
            .limit(page_size).all())
    return render_template("audit.html", rows=rows)


# ---------- Auth ----------

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


@app.route("/api/status")
@login_required
def api_status():
    """JSON con servicios y contenedores para refresco en vivo sin recargar."""
    containers, docker_err = docker_containers()
    pb = passbolt_status(os.environ.get("PASSBOLT_URL", ""))
    cmk = checkmk_status(
        os.environ.get("CHECKMK_URL", ""),
        os.environ.get("CHECKMK_USER", ""),
        os.environ.get("CHECKMK_SECRET", ""),
    )
    dbs = db_status(db.engine)
    problems, _ = find_problems()
    return {
        "services": [
            {"name": "Passbolt", "status": pb[0], "detail": pb[1]},
            {"name": "Checkmk", "status": cmk[0], "detail": cmk[1]},
            {"name": "DB central (MariaDB)", "status": dbs[0], "detail": dbs[1]},
        ],
        "containers": containers,
        "docker_err": docker_err,
        "problem_count": len(problems),
    }


# ---------- Bootstrap ----------

def _wait_for_db(retries: int = 30, delay: int = 2):
    for _ in range(retries):
        try:
            with app.app_context():
                db.session.execute(text("SELECT 1"))
            return True
        except OperationalError:
            time.sleep(delay)
    return False


def _bootstrap():
    with app.app_context():
        try:
            db.create_all()
        except OperationalError as e:
            # Race entre workers creando tablas a la vez — ya las creo el otro.
            if "already exists" not in str(e):
                raise

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
                db.session.rollback()


if _wait_for_db():
    _bootstrap()
else:
    raise RuntimeError("DB central no respondio a tiempo")
