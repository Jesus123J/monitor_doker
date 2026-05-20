"""
Worker que sincroniza mkmonitor con el estado real de los contenedores
del proyecto.

Que hace cada 60s:
  - Lee contenedores con docker_containers() (ya filtrado al proyecto)
  - Asegura una fila en mkmonitor.assets por contenedor (idempotente)
  - Si el contenedor esta caido (exited/unhealthy/restarting) y NO hay
    incidente abierto para ese asset -> crea uno
  - Si el contenedor esta sano y hay un incidente abierto -> lo cierra
    con closed_at=NOW()
"""
import os
import time
import threading
import logging

from sqlalchemy import text
from monitor import docker_containers

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = int(os.environ.get("MKMONITOR_POLL_INTERVAL", "60"))
LOCK_PATH = "/tmp/dashboard-mkmonitor.lock"


def _try_acquire_lock():
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def _release_lock():
    try:
        os.unlink(LOCK_PATH)
    except FileNotFoundError:
        pass


def _is_unhealthy(container):
    """Decide si un contenedor cuenta como problema operativo."""
    if container["status"] in ("exited", "dead", "restarting"):
        return True
    if container["health"] == "unhealthy":
        return True
    return False


def _problem_title(c):
    if c["status"] == "exited":
        return f"{c['name']}: contenedor exited (code={c.get('exit_code')})"
    if c["status"] == "restarting":
        return f"{c['name']}: restart loop"
    if c["health"] == "unhealthy":
        return f"{c['name']}: health=unhealthy"
    return f"{c['name']}: {c['status']}"


def _sync_once(app, db):
    containers, err = docker_containers()
    if err:
        logger.warning("mkmonitor: no pude listar contenedores: %s", err)
        return

    with app.app_context():
        for c in containers:
            host = c["name"]

            # 1) asegurar el asset
            db.session.execute(text("""
                INSERT INTO mkmonitor.assets
                    (hostname, type, criticality, owner_email, description)
                VALUES (:h, 'container', 'medium', 'jesus@utp.local',
                        :d)
                ON DUPLICATE KEY UPDATE description = VALUES(description)
            """), {"h": host, "d": f"Auto-detected from container {host}"})

            # 2) buscar incidente abierto
            row = db.session.execute(text("""
                SELECT i.id FROM mkmonitor.incidents i
                JOIN mkmonitor.assets a ON a.id = i.asset_id
                WHERE a.hostname = :h AND i.status = 'open'
                ORDER BY i.opened_at DESC LIMIT 1
            """), {"h": host}).fetchone()
            open_incident_id = row[0] if row else None

            unhealthy = _is_unhealthy(c)

            if unhealthy and not open_incident_id:
                # abrir incidente
                db.session.execute(text("""
                    INSERT INTO mkmonitor.incidents
                        (asset_id, title, description, severity, status)
                    SELECT a.id, :title, :desc,
                           CASE WHEN :h = 'unhealthy' OR :s IN ('exited','dead')
                                THEN 'critical' ELSE 'warning' END,
                           'open'
                    FROM mkmonitor.assets a WHERE a.hostname = :host
                """), {
                    "title": _problem_title(c),
                    "desc":  f"status={c['status']}, health={c['health']}, "
                             f"exit_code={c.get('exit_code')}, "
                             f"restart_count={c.get('restart_count')}",
                    "h": c["health"], "s": c["status"], "host": host,
                })
                logger.info("mkmonitor: incidente abierto -> %s", host)

            elif (not unhealthy) and open_incident_id:
                # cerrar
                db.session.execute(text("""
                    UPDATE mkmonitor.incidents
                       SET status = 'closed', closed_at = NOW()
                     WHERE id = :id
                """), {"id": open_incident_id})
                logger.info("mkmonitor: incidente cerrado <- %s", host)

        db.session.commit()


def _loop(app, db):
    while True:
        try:
            _sync_once(app, db)
        except Exception as e:
            logger.warning("mkmonitor loop error: %s", e)
        time.sleep(POLL_INTERVAL_SECONDS)


def start(app, db):
    if not _try_acquire_lock():
        logger.info("mkmonitor: ya hay otro worker corriendo")
        return False
    import atexit
    atexit.register(_release_lock)

    t = threading.Thread(target=_loop, args=(app, db), daemon=True)
    t.start()
    logger.info("mkmonitor auto-sync arrancado, poll=%ss", POLL_INTERVAL_SECONDS)
    return True
