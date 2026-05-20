"""
Recolectores y operaciones para el dashboard.

Lectura:
- Contenedores Docker via socket /var/run/docker.sock
- Healthchecks de Passbolt, Checkmk y la DB

Escritura (modo experto, solo admins):
- start/stop/restart de contenedores
"""
import os
import docker
import requests
from sqlalchemy import text, inspect


# ---------- Cliente docker compartido ----------

def _client():
    return docker.from_env()


_PROJECT_CACHE = None


def _detect_project():
    """Auto-detecta el compose project name.

    Lee el label del propio contenedor del dashboard via docker.sock.
    Asi funciona sin importar el nombre del folder donde clonaron el repo
    (monitor_doker, rene, lo-que-sea).
    """
    global _PROJECT_CACHE
    if _PROJECT_CACHE is not None:
        return _PROJECT_CACHE

    # 1) Override manual via env var
    explicit = os.environ.get("COMPOSE_PROJECT")
    if explicit:
        _PROJECT_CACHE = explicit
        return _PROJECT_CACHE

    # 2) Inspeccionar el propio contenedor por su hostname (= container ID)
    try:
        import socket
        hostname = socket.gethostname()
        self_container = _client().containers.get(hostname)
        project = self_container.labels.get("com.docker.compose.project")
        if project:
            _PROJECT_CACHE = project
            return project
    except Exception:
        pass

    # 3) Fallback
    _PROJECT_CACHE = "monitor_doker"
    return _PROJECT_CACHE


def _list_filters():
    """Filtro para listar solo contenedores del compose project."""
    if os.environ.get("SHOW_ALL_CONTAINERS", "0").lower() in ("1", "true", "yes"):
        return None
    return {"label": f"com.docker.compose.project={_detect_project()}"}


# ---------- Lectura de contenedores ----------

def docker_containers():
    try:
        containers = []
        filters = _list_filters()
        kwargs = {"all": True}
        if filters:
            kwargs["filters"] = filters
        for c in _client().containers.list(**kwargs):
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


def db_rows(host: str, db_name: str, table: str, password: str, limit: int = 25):
    """Devuelve las primeras N filas de una tabla, ocultando datos sensibles."""
    from sqlalchemy import create_engine
    import re

    # Whitelist defensiva
    if not re.match(r"^[a-zA-Z0-9_]+$", table):
        return {"error": "nombre de tabla invalido"}
    if db_name not in ("passbolt", "dashboard"):
        return {"error": "db invalida"}

    SENSITIVE = {
        # tabla -> {columna: estrategia}
        "users":           {"password_hash": "mask", "totp_secret": "mask"},
        "secrets":         {"data": "cipher"},
        "gpgkeys":         {"armored_key": "truncate"},
        "authentication_tokens": {"token": "mask"},
        "auth_logs":       {"user_agent": "truncate"},
    }

    def _mask(val, strategy):
        if val is None:
            return None
        s = str(val)
        if strategy == "mask":
            return "***"
        if strategy == "cipher":
            return "🔒 " + s[:60].replace("\n", " ") + "…"
        if strategy == "truncate":
            return s[:80] + "…" if len(s) > 80 else s
        return s

    try:
        url = (f"mysql+pymysql://root:{password}@{host}/{db_name}"
               "?charset=utf8mb4&connect_timeout=3")
        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            cols = [r[0] for r in conn.execute(
                text(f"SHOW COLUMNS FROM `{table}`"))]
            rows = []
            result = conn.execute(text(f"SELECT * FROM `{table}` LIMIT {limit}"))
            mask_map = SENSITIVE.get(table, {})
            for r in result:
                row = {}
                for i, c in enumerate(cols):
                    val = r[i]
                    if c in mask_map:
                        val = _mask(val, mask_map[c])
                    elif val is not None:
                        s = str(val)
                        val = s[:200] + "…" if len(s) > 200 else s
                    row[c] = val
                rows.append(row)
            return {"columns": cols, "rows": rows, "count": len(rows), "limit": limit}
    except Exception as e:
        return {"error": str(e)}


def db_activity_diff(db_host: str, mirror_host: str, db_password: str):
    """Compara conteos de filas entre db-central y db-mirror.

    Devuelve una lista por tabla relevante con central, mirror y diff.
    """
    from sqlalchemy import create_engine

    # Tablas que mas representan "actividad" del usuario
    targets = [
        ("passbolt",  "resources",  "Contrasenas guardadas en Passbolt"),
        ("passbolt",  "users",      "Usuarios de Passbolt"),
        ("passbolt",  "groups",     "Grupos de Passbolt"),
        ("dashboard", "users",      "Usuarios del panel"),
        ("dashboard", "audit_log",  "Acciones en bitacora"),
        ("dashboard", "container_lifecycle", "Eventos de ciclo de vida"),
    ]

    def _count(host, db, table):
        try:
            url = (f"mysql+pymysql://root:{db_password}@{host}/{db}"
                   "?charset=utf8mb4&connect_timeout=3")
            eng = create_engine(url, pool_pre_ping=True)
            with eng.connect() as conn:
                return int(conn.execute(text(
                    f"SELECT COUNT(*) FROM `{table}`"
                )).scalar() or 0)
        except Exception:
            return None

    out = []
    for db_name, table, label in targets:
        c = _count(db_host, db_name, table)
        m = _count(mirror_host, db_name, table)
        diff = None
        if c is not None and m is not None:
            diff = c - m
        out.append({
            "db": db_name, "table": table, "label": label,
            "central": c, "mirror": m, "diff": diff,
        })
    return out


def trigger_mirror_sync():
    """Ejecuta sync-now.sh dentro del contenedor db-sync."""
    try:
        c = _client().containers.get("db-sync")
        if c.status != "running":
            return False, f"db-sync no esta corriendo (estado={c.status})"
        result = c.exec_run("/usr/local/bin/sync-now.sh", demux=False)
        ok = result.exit_code == 0
        output = (result.output or b"").decode("utf-8", errors="replace")[-500:]
        return ok, output or ("OK" if ok else f"exit={result.exit_code}")
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


def checkmk_fetch_hosts(base_url: str, user: str, secret: str):
    """Devuelve lista de hosts con su estado consolidado desde la API.

    Usa /domain-types/host/collections/all con columns explicitas para
    traer el state, plugin_output y last_check.
    """
    if not (user and secret):
        return [], "Falta CMK_AUTOMATION_SECRET"

    api = f"{base_url.rstrip('/')}/check_mk/api/1.0"
    headers = {"Authorization": f"Bearer {user} {secret}"}

    try:
        r = requests.get(
            f"{api}/domain-types/host/collections/all",
            headers=headers,
            params={
                "columns": ["name", "state", "plugin_output",
                            "last_check", "acknowledged"],
            },
            timeout=10,
        )
        if r.status_code != 200:
            return [], f"API HTTP {r.status_code}: {r.text[:200]}"

        STATE_TEXT = {0: "OK", 1: "WARN", 2: "CRIT", 3: "UNKNOWN"}
        from datetime import datetime as _dt

        out = []
        for h in r.json().get("value", []):
            ext = h.get("extensions", {})
            last_check = ext.get("last_check")
            try:
                last_check_dt = _dt.utcfromtimestamp(int(last_check)) if last_check else None
            except Exception:
                last_check_dt = None
            state = ext.get("state")
            out.append({
                "name": ext.get("name") or h.get("id"),
                "state": int(state) if state is not None else None,
                "state_text": STATE_TEXT.get(int(state)) if state is not None else None,
                "output": (ext.get("plugin_output") or "")[:1000],
                "last_check": last_check_dt,
                "acknowledged": bool(ext.get("acknowledged")),
            })
        return out, None
    except Exception as e:
        return [], str(e)


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
                        "entries": [{"name": r[0], "rows": r[1] or 0} for r in rows],
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


def db_overview(db_host: str, db_user: str, db_password: str):
    """Lista las dos bases (passbolt y dashboard) con tablas y conteo de filas.

    Prueba visualmente que Passbolt SI esta usando la DB central.
    """
    from sqlalchemy import create_engine
    overview = []
    for db_name in ("passbolt", "dashboard"):
        entry = {"db": db_name, "tables": [], "total_rows": 0, "error": None}
        try:
            url = (f"mysql+pymysql://{db_user}:{db_password}@{db_host}/{db_name}"
                   "?charset=utf8mb4")
            eng = create_engine(url, pool_pre_ping=True)
            with eng.connect() as conn:
                rows = conn.execute(text(
                    "SELECT table_name, table_rows "
                    "FROM information_schema.tables WHERE table_schema=:s "
                    "ORDER BY table_name"
                ), {"s": db_name}).fetchall()
                for t_name, t_rows in rows:
                    n = t_rows or 0
                    entry["tables"].append({"name": t_name, "rows": n})
                    entry["total_rows"] += n
        except Exception as e:
            entry["error"] = str(e)
        overview.append(entry)
    return overview
