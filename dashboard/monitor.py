"""
Recolectores de estado para el dashboard.

- Contenedores Docker: via socket /var/run/docker.sock (montado read-only)
- Passbolt:           healthcheck HTTP a /healthcheck/status.json
- Checkmk:            Web API (necesita usuario "automation" + secret)
- DB central:         ping al motor MariaDB via SQLAlchemy
"""
import os
import docker
import requests
from sqlalchemy import text


def docker_containers():
    """Lista contenedores con su estado."""
    try:
        client = docker.from_env()
        containers = []
        for c in client.containers.list(all=True):
            health = "unknown"
            try:
                state = c.attrs.get("State", {})
                if state.get("Health"):
                    health = state["Health"].get("Status", "unknown")
                else:
                    health = state.get("Status", "unknown")
            except Exception:
                pass
            containers.append({
                "name": c.name,
                "image": (c.image.tags or ["<none>"])[0],
                "status": c.status,
                "health": health,
            })
        return containers, None
    except Exception as e:
        return [], str(e)


def passbolt_status(url: str):
    """Consulta el endpoint publico de health de Passbolt."""
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
    """Cuenta hosts en estado UP / DOWN / UNREACH usando la Web API REST."""
    if not (user and secret):
        return "unknown", "Falta CMK_AUTOMATION_SECRET"

    api = f"{base_url.rstrip('/')}/check_mk/api/1.0"
    headers = {"Authorization": f"Bearer {user} {secret}"}
    try:
        r = requests.get(
            f"{api}/domain-types/host/collections/all",
            headers=headers,
            timeout=5,
        )
        if r.status_code != 200:
            return "warning", f"API HTTP {r.status_code}"
        hosts = r.json().get("value", [])
        return "up", f"{len(hosts)} hosts monitoreados"
    except Exception as e:
        return "down", str(e)


def db_status(engine):
    """Ping a la DB central."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "up", "MariaDB responde"
    except Exception as e:
        return "down", str(e)
