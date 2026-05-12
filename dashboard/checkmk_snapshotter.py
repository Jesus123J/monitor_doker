"""
Worker que pollea la API de Checkmk cada N minutos y persiste snapshots
de cada host en dashboard.checkmk_snapshots.

Corre como thread daemon en el dashboard. Solo un worker de gunicorn lo
activa (lock por archivo) para evitar duplicados.
"""
import os
import time
import threading
import logging

from monitor import checkmk_fetch_hosts

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = int(os.environ.get("CHECKMK_POLL_INTERVAL", "300"))  # 5 min
LOCK_PATH = "/tmp/dashboard-checkmk.lock"


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


def _snapshot_once(app, db, CheckmkSnapshot):
    hosts, err = checkmk_fetch_hosts(
        base_url=os.environ.get("CHECKMK_URL", ""),
        user=os.environ.get("CHECKMK_USER", ""),
        secret=os.environ.get("CHECKMK_SECRET", ""),
    )
    if err:
        logger.warning("checkmk snapshot: %s", err)
        return
    if not hosts:
        return

    with app.app_context():
        for h in hosts:
            row = CheckmkSnapshot(
                host_name=h["name"],
                state=h["state"],
                state_text=h["state_text"],
                output=h["output"],
                last_check=h["last_check"],
                acknowledged=h["acknowledged"],
            )
            db.session.add(row)
        db.session.commit()
    logger.info("checkmk snapshot: %d hosts guardados", len(hosts))


def _loop(app, db, CheckmkSnapshot):
    while True:
        try:
            _snapshot_once(app, db, CheckmkSnapshot)
        except Exception as e:
            logger.warning("checkmk loop error: %s", e)
        time.sleep(POLL_INTERVAL_SECONDS)


def start_snapshotter(app, db, CheckmkSnapshot):
    if not _try_acquire_lock():
        logger.info("checkmk snapshotter: ya hay otro worker corriendo")
        return False

    import atexit
    atexit.register(_release_lock)

    t = threading.Thread(
        target=_loop, args=(app, db, CheckmkSnapshot), daemon=True
    )
    t.start()
    logger.info("checkmk snapshotter arrancado, poll=%ss", POLL_INTERVAL_SECONDS)
    return True
