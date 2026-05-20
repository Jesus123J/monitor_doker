"""
App unificadora: lee de las 4 bases (passbolt, dashboard, checkmk, mkmonitor)
en la misma instancia MariaDB, usando un usuario MySQL READ-ONLY.

No escribe nunca en passbolt, checkmk ni mkmonitor — solo lee.
Solo el dashboard tiene un usuario con permisos de escritura sobre `dashboard`.
"""
import os
from sqlalchemy import create_engine, text


def _reader_engine():
    """Engine que usa el usuario 'unified_reader' (solo SELECT)."""
    host = os.environ.get("DB_HOST", "db-central")
    user = os.environ.get("UNIFIED_READER_USER", "unified_reader")
    pwd = os.environ.get("UNIFIED_READER_PASSWORD", "")
    url = (f"mysql+pymysql://{user}:{pwd}@{host}/?charset=utf8mb4"
           "&connect_timeout=5")
    return create_engine(url, pool_pre_ping=True)


def unified_overview():
    """Devuelve un dict con conteo de tablas clave en cada base."""
    eng = _reader_engine()
    out = {"passbolt": {}, "checkmk": {}, "mkmonitor": {}, "dashboard": {}}
    with eng.connect() as conn:
        for db, tables in (
            ("passbolt",  ["users", "resources", "secrets",
                           "permissions", "groups", "action_logs"]),
            ("checkmk",   ["host_snapshots"]),
            ("mkmonitor", ["assets", "alerts", "incidents", "contacts"]),
            ("dashboard", ["users", "audit_log", "container_lifecycle",
                           "checkmk_snapshots"]),
        ):
            for t in tables:
                try:
                    n = conn.execute(text(
                        f"SELECT COUNT(*) FROM `{db}`.`{t}`"
                    )).scalar() or 0
                    out[db][t] = int(n)
                except Exception:
                    out[db][t] = None
    return out


def cross_resources_to_hosts():
    """
    Query CRUZADA — ejemplo principal de unificacion.

    Para cada recurso de Passbolt (contrasena guardada) trata de encontrar:
      - el host correspondiente en checkmk.host_snapshots (ultimo estado)
      - el asset correspondiente en mkmonitor.assets

    Match por hostname (extraido del URI de Passbolt).
    """
    sql = text("""
        SELECT
            pb.id           AS passbolt_resource_id,
            pb.name         AS resource_name,
            pb.username     AS resource_user,
            pb.uri          AS resource_uri,
            cm.host_name    AS checkmk_host,
            cm.state_text   AS checkmk_state,
            cm.snapshot_at  AS checkmk_last_seen,
            mk.id           AS mkmonitor_asset_id,
            mk.hostname     AS mkmonitor_hostname,
            mk.criticality  AS mkmonitor_criticality,
            mk.owner_email  AS mkmonitor_owner
        FROM passbolt.resources pb
        LEFT JOIN (
            SELECT host_name, state_text, snapshot_at
            FROM checkmk.host_snapshots
            WHERE id IN (
                SELECT MAX(id) FROM checkmk.host_snapshots GROUP BY host_name
            )
        ) cm ON pb.uri LIKE CONCAT('%', cm.host_name, '%')
        LEFT JOIN mkmonitor.assets mk
               ON mk.hostname = cm.host_name
              OR pb.uri LIKE CONCAT('%', mk.hostname, '%')
        WHERE pb.deleted = 0
        ORDER BY pb.created DESC
        LIMIT 50
    """)
    eng = _reader_engine()
    with eng.connect() as conn:
        result = conn.execute(sql)
        return [dict(r._mapping) for r in result]


def open_incidents_with_passbolt_creds():
    """
    Otra query util: incidentes abiertos en MKMonitor cruzados contra
    si tenemos credenciales en Passbolt para ese host.
    """
    sql = text("""
        SELECT
            i.id           AS incident_id,
            i.title,
            i.severity,
            i.opened_at,
            a.hostname,
            a.criticality,
            (
                SELECT COUNT(*)
                FROM passbolt.resources pb
                WHERE pb.deleted = 0
                  AND pb.uri LIKE CONCAT('%', a.hostname, '%')
            ) AS credentials_in_passbolt,
            c.name         AS assigned_name,
            c.email        AS assigned_email
        FROM mkmonitor.incidents i
        LEFT JOIN mkmonitor.assets   a ON a.id = i.asset_id
        LEFT JOIN mkmonitor.contacts c ON c.id = i.assigned_to
        WHERE i.status = 'open'
        ORDER BY i.opened_at DESC
    """)
    eng = _reader_engine()
    with eng.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql)]


def assets_health_summary():
    """Resumen para /unified: assets MKMonitor + estado real del contenedor + Checkmk."""
    sql = text("""
        SELECT
            a.id,
            a.hostname,
            a.ip_address,
            a.type,
            a.criticality,
            -- Estado actual del contenedor (de la ultima fila en lifecycle)
            cl.new_state   AS container_state,
            cl.health      AS container_health,
            cl.created_at  AS state_updated,
            -- Estado de Checkmk (solo se llena si es un host monitoreado allá)
            cm.state_text  AS checkmk_state,
            cm.snapshot_at AS checkmk_last_seen,
            (
                SELECT COUNT(*) FROM mkmonitor.incidents i
                WHERE i.asset_id = a.id AND i.status = 'open'
            ) AS open_incidents,
            (
                SELECT COUNT(*) FROM passbolt.resources pb
                WHERE pb.deleted = 0 AND pb.uri LIKE CONCAT('%', a.hostname, '%')
            ) AS passbolt_resources
        FROM mkmonitor.assets a
        LEFT JOIN (
            SELECT container_name, new_state, health, created_at
            FROM dashboard.container_lifecycle
            WHERE id IN (
                SELECT MAX(id) FROM dashboard.container_lifecycle
                GROUP BY container_name
            )
        ) cl ON cl.container_name = a.hostname
        LEFT JOIN (
            SELECT host_name, state_text, snapshot_at
            FROM checkmk.host_snapshots
            WHERE id IN (
                SELECT MAX(id) FROM checkmk.host_snapshots GROUP BY host_name
            )
        ) cm ON cm.host_name = a.hostname
        ORDER BY FIELD(a.criticality,'critical','high','medium','low'),
                 a.hostname
    """)
    eng = _reader_engine()
    with eng.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql)]
