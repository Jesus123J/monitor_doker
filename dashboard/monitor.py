"""
Recolectores y operaciones para el dashboard.

Lectura:
- Contenedores Docker via socket /var/run/docker.sock
- Healthchecks de Passbolt, Checkmk y la DB

Escritura (modo experto, solo admins):
- start/stop/restart de contenedores
"""
import docker
import requests
from sqlalchemy import text, inspect


# ---------- Cliente docker compartido ----------

def _client():
    return docker.from_env()


# ---------- Lectura de contenedores ----------

def docker_containers():
    try:
        containers = []
        for c in _client().containers.list(all=True):
            state = c.attrs.get("State", {})
            health = "n/a"
            if state.get("Health"):
                health = state["Health"].get("Status", "n/a")

            restart_count = state.get("RestartCount", 0)
            exit_code = state.get("ExitCode", 0)

            containers.append({
                "name": c.name,
                "id_short": c.short_id,
                "image": (c.image.tags or ["<none>"])[0],
                "status": c.status,
                "health": health,
                "restart_count": restart_count,
                "exit_code": exit_code,
            })
        return containers, None
    except Exception as e:
        return [], str(e)


def container_logs(name: str, lines: int = 200) -> str:
    try:
        c = _client().containers.get(name)
        return c.logs(tail=lines, timestamps=True).decode("utf-8", errors="replace")
    except Exception as e:
        return f"[error obteniendo logs: {e}]"


def container_stats(name: str):
    """Stats one-shot: CPU%, mem usada/limite, red rx/tx."""
    try:
        c = _client().containers.get(name)
        s = c.stats(stream=False)

        cpu_delta = (s["cpu_stats"]["cpu_usage"]["total_usage"]
                     - s["precpu_stats"]["cpu_usage"]["total_usage"])
        sys_delta = (s["cpu_stats"].get("system_cpu_usage", 0)
                     - s["precpu_stats"].get("system_cpu_usage", 0))
        cpu_pct = 0.0
        if sys_delta > 0 and cpu_delta > 0:
            ncpus = s["cpu_stats"].get("online_cpus") or len(
                s["cpu_stats"]["cpu_usage"].get("percpu_usage") or [1]
            )
            cpu_pct = (cpu_delta / sys_delta) * ncpus * 100.0

        mem_used = s["memory_stats"].get("usage", 0)
        mem_limit = s["memory_stats"].get("limit", 1)
        mem_pct = (mem_used / mem_limit) * 100.0 if mem_limit else 0.0

        nets = s.get("networks", {})
        rx = sum(n.get("rx_bytes", 0) for n in nets.values())
        tx = sum(n.get("tx_bytes", 0) for n in nets.values())

        return {
            "cpu_pct": round(cpu_pct, 2),
            "mem_used_mb": round(mem_used / 1024 / 1024, 1),
            "mem_limit_mb": round(mem_limit / 1024 / 1024, 1),
            "mem_pct": round(mem_pct, 2),
            "net_rx_mb": round(rx / 1024 / 1024, 2),
            "net_tx_mb": round(tx / 1024 / 1024, 2),
        }
    except Exception as e:
        return {"error": str(e)}


def container_inspect(name: str):
    """Datos del inspect, ocultando env vars que parezcan secrets."""
    try:
        c = _client().containers.get(name)
        a = c.attrs
        env = a.get("Config", {}).get("Env", []) or []
        env_safe = []
        for e in env:
            key, _, val = e.partition("=")
            if any(k in key.upper() for k in ("PASSWORD", "SECRET", "KEY", "TOKEN")):
                val = "***"
            env_safe.append(f"{key}={val}")

        mounts = [
            f"{m.get('Source','?')} -> {m.get('Destination','?')} ({m.get('Mode','')})"
            for m in a.get("Mounts", [])
        ]
        nets = list((a.get("NetworkSettings", {}).get("Networks") or {}).keys())

        return {
            "name": c.name,
            "id_short": c.short_id,
            "image": (c.image.tags or ["<none>"])[0],
            "created": a.get("Created", ""),
            "started_at": a.get("State", {}).get("StartedAt", ""),
            "status": c.status,
            "restart_count": a.get("RestartCount", 0),
            "env": env_safe,
            "mounts": mounts,
            "networks": nets,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------- Escritura (admin) ----------

def container_action(name: str, action: str):
    if action not in {"start", "stop", "restart"}:
        return False, f"Accion invalida: {action}"
    try:
        c = _client().containers.get(name)
        getattr(c, action)()
        return True, f"{action} OK sobre {name}"
    except Exception as e:
        return False, str(e)


# ---------- Diagnostico ----------

def find_problems():
    """Lista contenedores con sintomas de problema."""
    items, err = docker_containers()
    if err:
        return [], err

    problems = []
    for c in items:
        reasons = []
        if c["status"] == "exited" and c["exit_code"] != 0:
            reasons.append(f"exited con code={c['exit_code']}")
        if c["status"] == "restarting":
            reasons.append("en restart loop")
        if c["health"] == "unhealthy":
            reasons.append("health=unhealthy")
        if c["restart_count"] >= 3:
            reasons.append(f"restart_count={c['restart_count']}")

        if reasons:
            problems.append({
                "name": c["name"],
                "image": c["image"],
                "status": c["status"],
                "reasons": reasons,
            })
    return problems, None


# ---------- Healthchecks de servicios ----------

def passbolt_status(url: str):
    try:
        r = requests.get(
            f"{url.rstrip('/')}/healthcheck/status.json",
            timeout=5,
            verify=False,
        )
        if r.status_code == 200 and r.json().get("body") == "OK":
            return "up", "Passbolt responde OK"
        return "warning", f"HTTP {r.status_code}"
    except Exception as e:
        return "down", str(e)


def checkmk_status(base_url: str, user: str, secret: str):
    if not (user and secret):
        return "unknown", "Falta CMK_AUTOMATION_SECRET"
    api = f"{base_url.rstrip('/')}/check_mk/api/1.0"
    headers = {"Authorization": f"Bearer {user} {secret}"}
    try:
        r = requests.get(
            f"{api}/domain-types/host/collections/all",
            headers=headers, timeout=5,
        )
        if r.status_code != 200:
            return "warning", f"API HTTP {r.status_code}"
        hosts = r.json().get("value", [])
        return "up", f"{len(hosts)} hosts monitoreados"
    except Exception as e:
        return "down", str(e)


def db_status(engine):
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "up", "MariaDB responde"
    except Exception as e:
        return "down", str(e)


# ---------- Estado del mirror DB ----------

def mirror_status(status_file: str, mirror_host: str, mirror_user: str, mirror_password: str):
    """Lee el archivo de estado del db-sync y consulta tablas/filas en el mirror."""
    import os
    from datetime import datetime, timezone

    info = {"last_sync": None, "result": "unknown", "elapsed": None,
            "age_minutes": None, "tables": []}

    if os.path.isfile(status_file):
        try:
            with open(status_file) as f:
                line = f.read().strip()
            # Formato: "OK 2026-05-10T... elapsed=Xs dbs=passbolt dashboard"
            #     o:  "FAIL 2026-05-10T..."
            parts = line.split()
            info["result"] = parts[0]
            info["last_sync"] = parts[1]
            for p in parts[2:]:
                if p.startswith("elapsed="):
                    info["elapsed"] = p.split("=", 1)[1]
            ts = datetime.fromisoformat(info["last_sync"])
            now = datetime.now(timezone.utc).astimezone(ts.tzinfo)
            info["age_minutes"] = round((now - ts).total_seconds() / 60, 1)
        except Exception as e:
            info["error_status"] = str(e)

    # Consultar tablas en el mirror
    try:
        from sqlalchemy import create_engine
        url = f"mysql+pymysql://{mirror_user}:{mirror_password}@{mirror_host}/?charset=utf8mb4"
        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            for db_name in ("passbolt", "dashboard"):
                try:
                    rows = conn.execute(text(
                        "SELECT table_name, table_rows "
                        "FROM information_schema.tables WHERE table_schema=:s "
                        "ORDER BY table_name"
                    ), {"s": db_name}).fetchall()
                    info["tables"].append({
                        "db": db_name,
                        "items": [{"name": r[0], "rows": r[1] or 0} for r in rows],
                    })
                except Exception as e:
                    info["tables"].append({"db": db_name, "error": str(e)})
    except Exception as e:
        info["mirror_error"] = str(e)

    return info


# ---------- Esquema de la DB ----------

def db_tables(engine):
    """Lista tablas de la DB del dashboard con conteo de filas."""
    try:
        insp = inspect(engine)
        result = []
        with engine.connect() as conn:
            for tname in insp.get_table_names():
                cols = [
                    {"name": c["name"], "type": str(c["type"]),
                     "nullable": c.get("nullable", True)}
                    for c in insp.get_columns(tname)
                ]
                count = conn.execute(text(f"SELECT COUNT(*) FROM `{tname}`")).scalar()
                result.append({"name": tname, "rows": count, "columns": cols})
        return result, None
    except Exception as e:
        return [], str(e)
