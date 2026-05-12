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


class AuditLog(db.Model):
    """Bitacora de acciones operativas (start/stop/restart de contenedores)."""
    __tablename__ = "audit_log"

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    username = db.Column(db.String(64), nullable=False)
    action = db.Column(db.String(32), nullable=False)
    target = db.Column(db.String(128), nullable=False)
    success = db.Column(db.Boolean, nullable=False, default=True)
    detail = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship("User", backref="audit_logs")


class CheckmkSnapshot(db.Model):
    """Snapshot periodico del estado de un host segun la API de Checkmk."""
    __tablename__ = "checkmk_snapshots"

    id = db.Column(db.BigInteger, primary_key=True)
    snapshot_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    host_name = db.Column(db.String(128), nullable=False, index=True)
    state = db.Column(db.Integer)  # 0=OK, 1=WARN, 2=CRIT, 3=UNKNOWN
    state_text = db.Column(db.String(16))
    output = db.Column(db.Text)
    last_check = db.Column(db.DateTime, nullable=True)
    acknowledged = db.Column(db.Boolean, default=False)

    __table_args__ = (
        db.Index("idx_host_time", "host_name", "snapshot_at"),
    )


class ContainerLifecycle(db.Model):
    """Eventos de ciclo de vida de contenedores: cuando arranca, se apaga, etc."""
    __tablename__ = "container_lifecycle"

    id = db.Column(db.BigInteger, primary_key=True)
    container_name = db.Column(db.String(128), nullable=False, index=True)
    prev_state = db.Column(db.String(32))
    new_state = db.Column(db.String(32), nullable=False)
    health = db.Column(db.String(32))
    duration_seconds = db.Column(db.Integer)  # cuanto duro el estado previo
    detail = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
