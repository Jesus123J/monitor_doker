from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(128), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class MonitoredTarget(db.Model):
    __tablename__ = "monitored_targets"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    kind = db.Column(
        db.Enum("container", "passbolt", "checkmk", "db", "custom"),
        nullable=False,
    )
    endpoint = db.Column(db.String(255))
    description = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class StatusLog(db.Model):
    __tablename__ = "status_log"

    id = db.Column(db.BigInteger, primary_key=True)
    target_id = db.Column(
        db.Integer,
        db.ForeignKey("monitored_targets.id", ondelete="CASCADE"),
        nullable=False,
    )
    status = db.Column(
        db.Enum("up", "down", "warning", "unknown"), nullable=False
    )
    detail = db.Column(db.Text)
    checked_at = db.Column(db.DateTime, default=datetime.utcnow)

    target = db.relationship("MonitoredTarget", backref="logs")
