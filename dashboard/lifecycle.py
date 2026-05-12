"""
Worker que detecta transiciones de estado de los contenedores y las
registra en la tabla container_lifecycle.

Corre como thread daemon dentro del proceso del dashboard. Solo uno de
los workers de gunicorn lo activa (usa un lock por archivo) para evitar
duplicados.
"""
import os
import time
import threading
import logging
from datetime import datetime

import docker

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = int(os.environ.get("LIFECYCLE_POLL_INTERVAL", "30"))
LOCK_PATH = "/tmp/dashboard-lifecycle.lock"


def _read_state():
    client = docker.from_env()
    out = {}
    for c in client.containers.list(all=True):
        state = c.attrs.get("State", {})
        health = "n/a"
        if state.get("Health"):
            health = state["Health"].get("Status", "n/a")
        out[c.name] = {
            "status": c.status,
            "health": health,
            "ts": time.time(),
        }
    return out


def _record(app, db, ContainerLifecycle, name, prev, current):
    duration = None
    if prev and prev.get("ts"):
        duration = int(current["ts"] - prev["ts"])

    detail = None
    if prev is None:
        detail = "primer registro"
    elif prev["status"] != current["status"]:
        detail = f"{prev['status']} -> {current['status']}"
    elif prev["health"] != current["health"]:
        detail = f"health {prev['health']} -> {current['health']}"

    if detail is None:
        return  # sin cambios

    with app.app_context():
        entry = ContainerLifecycle(
            container_name=name,
            prev_state=(prev or {}).get("status"),
            new_state=current["status"],
            health=current["health"],
            duration_seconds=duration,
            detail=detail[:255],
        )
        db.session.add(entry)
        db.session.commit()
    logger.info("lifecycle: %s %s", name, detail)


def _try_acquire_lock():
    """Solo un worker corre el tracker. Usa O_EXCL en un archivo de lock."""
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


def _loop(app, db, ContainerLifecycle):
    last = {}
    while True:
        try:
            current = _read_state()
            # Detectar cambios y registrar
            for name, info in current.items():
                _record(app, db, ContainerLifecycle, name, last.get(name), info)
            # Detectar contenedores que desaparecieron (poco comun, pero por completitud)
            for name in last.keys() - current.keys():
                _record(app, db, ContainerLifecycle, name, last[name],
                        {"status": "removed", "health": "n/a", "ts": time.time()})
            last = current
        except Exception as e:
            logger.warning("lifecycle tracker: %s", e)
        time.sleep(POLL_INTERVAL_SECONDS)


def start_tracker(app, db, ContainerLifecycle):
    """Arranca el thread si todavia no se arranco en otro worker."""
    if not _try_acquire_lock():
        logger.info("lifecycle tracker: ya hay otro worker corriendo")
        return False

    # Liberar el lock cuando el proceso muere
    import atexit
    atexit.register(_release_lock)

    t = threading.Thread(
        target=_loop, args=(app, db, ContainerLifecycle), daemon=True
    )
    t.start()
    logger.info("lifecycle tracker arrancado, poll=%ss", POLL_INTERVAL_SECONDS)
    return True
