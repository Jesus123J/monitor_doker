# Unificación de bases de datos

Diseño de cómo este proyecto unifica 3 sistemas (Passbolt + CheckMK + MKMonitor)
en un solo motor MariaDB, **manteniendo schemas separados**, con una **app web
agregadora de solo lectura** que cruza datos entre las 3.

> Documento exhaustivo respondiendo los 10 puntos del prompt original.
> Para una vista mas corta: [`ARQUITECTURA.md`](ARQUITECTURA.md).

---

## 1. Arquitectura recomendada

```
                            ┌──────────────────┐
                            │  Usuario / Web   │
                            └────────┬─────────┘
                                     │
                            ┌────────▼─────────┐
                            │      nginx       │
                            └────┬─────────┬───┘
                                 │         │
                ┌────────────────┘         └─────────────┐
                │                                        │
        ┌───────▼──────┐                          ┌──────▼─────────┐
        │   Passbolt   │                          │   Dashboard    │
        │   (escribe)  │                          │   (Flask)      │
        └───────┬──────┘                          │  - escribe en  │
                │                                 │    'dashboard' │
                │                                 │  - LEE de las  │
                │                                 │    otras 3 con │
                │                                 │ unified_reader │
                │                                 └──┬─────────────┘
                │                                    │
                │   ┌────────────────────────────────┴───────┐
                │   │                                        │
                ▼   ▼                                        ▼
        ┌────────────────────────────────────────┐   ┌─────────────┐
        │       db-central (MariaDB 11)          │   │   Checkmk   │
        │  ┌──────────┐ ┌──────────┐             │   │ (RRDtool +  │
        │  │ passbolt │ │ dashboard│             │◄──┤  SQLite     │
        │  └──────────┘ └──────────┘             │   │  internos)  │
        │  ┌──────────┐ ┌──────────┐             │   └──────┬──────┘
        │  │ checkmk  │ │mkmonitor │             │          │
        │  └──────────┘ └──────────┘             │          │
        │   ▲ (snapshots                          │          │
        │     via API)                            │          │
        └─────────────────┬───────────────────────┘          │
                          │                                  │
                          │ db-sync cada 1h                  │
                          ▼                                  │
        ┌────────────────────────────────────────┐          │
        │       db-mirror (copia de seguridad)   │          │
        └────────────────────────────────────────┘          │
                                                            │
        ┌───────────────────────────────────────────────────┘
        │  El snapshotter llama la API REST de Checkmk
        ▼  cada 5 min y persiste estado en checkmk.host_snapshots.
```

### Componentes

| Componente | Rol |
|---|---|
| **MariaDB único** (`db-central`) | Motor compartido. 4 bases separadas |
| **db-mirror** | Réplica al pie (dump+restore cada 1h) |
| **Passbolt** | Escribe en su DB `passbolt` |
| **Dashboard (Flask)** | Escribe en `dashboard`. Lee en las 4 con usuario `unified_reader` |
| **Checkmk** | No habla SQL — la app agregadora lo consulta vía API REST y guarda snapshots |
| **MKMonitor** (esquema propio) | Base `mkmonitor` con assets/incidents/alerts |

---

## 2. ¿Esquema unificado o bases separadas?

**Bases separadas, en el mismo motor MariaDB.**

| Enfoque | Pros | Contras | Veredicto |
|---|---|---|---|
| **Una base "todo junto"** | Joins triviales | Conflictos de nombres, migraciones rompen todo, mezcla de cifrados y formatos | ❌ NO hacer |
| **Bases separadas, mismo motor** ⭐ | Joins cross-schema funcionan (MariaDB lo permite nativo), backups independientes, cada app conserva sus migraciones | Necesita gobernanza de permisos | ✅ Lo que usamos |
| **Bases separadas en motores distintos** | Aislamiento total | Hay que abrir conexiones por separado, joins solo a nivel app | Para entornos críticos |

**MariaDB permite `JOIN` entre schemas del mismo motor sin nada especial:**
```sql
SELECT pb.uri, cm.state_text
FROM passbolt.resources pb
JOIN checkmk.host_snapshots cm ON pb.uri LIKE CONCAT('%', cm.host_name, '%');
```

---

## 3. Estrategia de lectura

La app agregadora abre **una sola conexión** con un usuario read-only y usa
`schema.table` para todas las queries.

### SQLAlchemy (lo que usa este proyecto)

```python
# dashboard/unified.py
from sqlalchemy import create_engine, text
import os

def _reader_engine():
    host = os.environ.get("DB_HOST", "db-central")
    user = os.environ.get("UNIFIED_READER_USER", "unified_reader")
    pwd  = os.environ.get("UNIFIED_READER_PASSWORD", "")
    url  = f"mysql+pymysql://{user}:{pwd}@{host}/?charset=utf8mb4"
    return create_engine(url, pool_pre_ping=True)

# Sin default DB en el URL: las queries usan schema.table siempre.
eng = _reader_engine()
with eng.connect() as conn:
    rows = conn.execute(text("""
        SELECT pb.name, cm.state_text, mk.criticality
        FROM passbolt.resources pb
        LEFT JOIN checkmk.host_snapshots cm ON pb.uri LIKE CONCAT('%', cm.host_name, '%')
        LEFT JOIN mkmonitor.assets       mk ON mk.hostname = cm.host_name
        WHERE pb.deleted = 0
    """)).fetchall()
```

### PHP / PDO equivalente

```php
$pdo = new PDO(
    'mysql:host=db-central;charset=utf8mb4',
    'unified_reader',
    getenv('UNIFIED_READER_PASSWORD'),
    [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]
);

$rows = $pdo->query("
    SELECT pb.name, cm.state_text
    FROM passbolt.resources pb
    LEFT JOIN checkmk.host_snapshots cm
      ON pb.uri LIKE CONCAT('%', cm.host_name, '%')
    WHERE pb.deleted = 0
")->fetchAll(PDO::FETCH_ASSOC);
```

### Node.js / mysql2

```javascript
const mysql = require('mysql2/promise');
const conn = await mysql.createConnection({
    host: 'db-central',
    user: 'unified_reader',
    password: process.env.UNIFIED_READER_PASSWORD,
});

const [rows] = await conn.query(`
    SELECT pb.name, cm.state_text
    FROM passbolt.resources pb
    LEFT JOIN checkmk.host_snapshots cm
      ON pb.uri LIKE CONCAT('%', cm.host_name, '%')
    WHERE pb.deleted = 0
`);
```

> Nota: el cliente NO especifica una "default DB". Todas las queries son
> `<schema>.<table>` para que el motor sepa de qué base sacar.

---

## 4. Qué NO hacer

| ❌ Error | Por qué |
|---|---|
| Modificar columnas o agregar índices manualmente en `passbolt` o `checkmk` | Las migraciones de la app fuente fallarán en la próxima versión |
| Conectar el agregador con el usuario `root` | Cualquier bug puede romper todo. Read-only por defecto |
| Hacer `INSERT/UPDATE/DELETE` desde el agregador a `passbolt` o `checkmk` | Compromete las garantías de cada producto (cifrado E2E de Passbolt, consistencia de tiempo de Checkmk) |
| Hacer joins por IDs entre apps distintas | Cada app tiene su espacio de IDs. Cruzar por valores semánticos (hostname, URI) |
| Tratar de meter las métricas de Checkmk (RRDtool) en MySQL | Costo de performance enorme. Persisten **snapshots** o **estado actual**, no las series crudas |
| Hacer el dump de las 4 bases por separado en momentos distintos | Inconsistencia entre bases. Hacer un solo `mariadb-dump --databases` para tener snapshot consistente |
| Hardcodear passwords en el código | Usar `.env` + permisos del filesystem |

---

## 5. Cruce de datos útil

### A. "Hosts que monitoreo en Checkmk y para los que tengo credenciales en Passbolt"

```sql
SELECT
    cm.host_name,
    cm.state_text   AS estado_actual,
    COUNT(pb.id)    AS credenciales_disponibles
FROM (
    SELECT host_name, state_text, snapshot_at
    FROM checkmk.host_snapshots
    WHERE id IN (SELECT MAX(id) FROM checkmk.host_snapshots GROUP BY host_name)
) cm
LEFT JOIN passbolt.resources pb
       ON pb.deleted = 0
      AND pb.uri LIKE CONCAT('%', cm.host_name, '%')
GROUP BY cm.host_name, cm.state_text
ORDER BY credenciales_disponibles DESC;
```

### B. "Incidentes abiertos cuyo host no tiene credenciales registradas"

```sql
SELECT i.title, a.hostname, i.severity, i.opened_at
FROM mkmonitor.incidents i
JOIN mkmonitor.assets a ON a.id = i.asset_id
WHERE i.status = 'open'
  AND NOT EXISTS (
      SELECT 1 FROM passbolt.resources pb
      WHERE pb.deleted = 0 AND pb.uri LIKE CONCAT('%', a.hostname, '%')
  );
```

### C. "Assets críticos en estado CRIT en Checkmk"

```sql
SELECT a.hostname, a.criticality, cm.state_text, cm.output
FROM mkmonitor.assets a
JOIN (
    SELECT host_name, state_text, output
    FROM checkmk.host_snapshots
    WHERE id IN (SELECT MAX(id) FROM checkmk.host_snapshots GROUP BY host_name)
) cm ON cm.host_name = a.hostname
WHERE a.criticality = 'critical' AND cm.state_text = 'CRIT';
```

### D. "Actividad de un usuario Passbolt + acciones operativas que hizo en el dashboard"

```sql
SELECT
    al.created            AS cuando,
    pu.username           AS quien,
    a.name                AS accion_passbolt,
    NULL                  AS accion_dashboard
FROM passbolt.action_logs al
JOIN passbolt.actions a  ON a.id = al.action_id
JOIN passbolt.users   pu ON pu.id = al.user_id
WHERE pu.username = 'jesus@utp.local'
UNION ALL
SELECT
    audit.created_at, audit.username, NULL, audit.action
FROM dashboard.audit_log audit
WHERE audit.username = 'admin'
ORDER BY cuando DESC LIMIT 100;
```

> En este proyecto: `/unified` los muestra ya formateados en HTML.
> Endpoint JSON equivalente: `/api/unified`.

---

## 6. Backup / restore

### Backup consistente de las 4 bases

```bash
bash scripts/backup.sh                  # default: ./backups/
bash scripts/backup.sh /var/backups     # destino custom
```

El script hace un solo `mariadb-dump` con:
- `--single-transaction` (no lockea InnoDB)
- `--databases passbolt dashboard checkmk mkmonitor`
- `--add-drop-database` (el restore reemplaza limpio)
- Comprime con gzip
- Rota: mantiene los últimos 14 archivos

### Restore

```bash
gunzip < backups/db-central-20260514_103000.sql.gz | \
    docker exec -i db-central mariadb -uroot -p"$DB_ROOT_PASSWORD"
```

### Réplica continua (automatizada)

Ya está el servicio `db-sync` que cada **1 hora** hace `mariadb-dump` de las 4 bases y lo restaura en `db-mirror`. Eso te da un "backup en línea" sin tener que correr el script.

---

## 7. MKMonitor — supuestos de esquema

Como no me pasaste el esquema real, asumí uno típico de "sistema de monitoreo" y
está creado en `db/init/02-unified-schemas.sh`:

| Tabla | Columnas clave |
|---|---|
| `assets` | id, hostname, ip_address, type (server/router/vm/...), criticality (low→critical), owner_email |
| `contacts` | id, name, email, phone, role, on_call |
| `incidents` | id, asset_id, title, description, severity, status (open/acknowledged/closed), assigned_to, opened_at, closed_at |
| `alerts` | id, asset_id, metric, value, threshold, level (warning/critical), fired_at, resolved_at |

Si tu MKMonitor real tiene otro esquema:

1. **No toques las tablas existentes** — agregale las suyas como esquema adicional
2. Edita `db/init/02-unified-schemas.sh` con el `CREATE TABLE` real
3. Actualiza las queries en `dashboard/unified.py` con los nombres correctos
4. El usuario `unified_reader` ya tiene `SELECT ON mkmonitor.*` así que cualquier tabla nueva queda accesible automáticamente

---

## 8. Seguridad (permisos)

El usuario `unified_reader` se crea con **solo SELECT** sobre todas las bases:

```sql
CREATE USER 'unified_reader'@'%' IDENTIFIED BY '...';
GRANT SELECT ON passbolt.*  TO 'unified_reader'@'%';
GRANT SELECT ON dashboard.* TO 'unified_reader'@'%';
GRANT SELECT ON checkmk.*   TO 'unified_reader'@'%';
GRANT SELECT ON mkmonitor.* TO 'unified_reader'@'%';
```

**Si el código del agregador trata de hacer UPDATE/INSERT/DELETE → error inmediato del motor.** Es defensa en profundidad.

### Otros usuarios MySQL del stack

| Usuario | Permisos | Lo usa |
|---|---|---|
| `root` | ALL | Solo administración manual |
| `passbolt` | ALL en `passbolt.*` | Servicio Passbolt |
| `dashboard` | ALL en `dashboard.*` | Servicio dashboard |
| `unified_reader` | **SELECT** en passbolt + dashboard + checkmk + mkmonitor | App agregadora |

Verificación rápida:
```bash
docker exec db-central mariadb -uunified_reader -p"$UNIFIED_READER_PASSWORD" -e "INSERT INTO passbolt.users (username) VALUES ('x');"
# ERROR 1142 (42000): INSERT command denied to user 'unified_reader'@... ← correcto
```

---

## 9. docker-compose.yml (lo que ya tiene este repo)

Los servicios relevantes:

```yaml
services:

  db-central:
    image: mariadb:11
    environment:
      MYSQL_ROOT_PASSWORD: ${DB_ROOT_PASSWORD}
      MYSQL_DATABASE: passbolt
      MYSQL_USER: ${PASSBOLT_DB_USER}
      MYSQL_PASSWORD: ${PASSBOLT_DB_PASSWORD}
      DASHBOARD_DB_USER: ${DASHBOARD_DB_USER}
      DASHBOARD_DB_PASSWORD: ${DASHBOARD_DB_PASSWORD}
      UNIFIED_READER_PASSWORD: ${UNIFIED_READER_PASSWORD}
    volumes:
      - db_data:/var/lib/mysql
      - ./db/init:/docker-entrypoint-initdb.d:ro  # ← crea las 4 bases + reader
    ports:
      - "127.0.0.1:3307:3306"  # para Workbench desde el host

  passbolt:
    image: passbolt/passbolt:latest-ce
    depends_on: { db-central: { condition: service_healthy } }
    environment:
      DATASOURCES_DEFAULT_HOST: db-central
      DATASOURCES_DEFAULT_USERNAME: ${PASSBOLT_DB_USER}
      DATASOURCES_DEFAULT_PASSWORD: ${PASSBOLT_DB_PASSWORD}
      DATASOURCES_DEFAULT_DATABASE: passbolt

  dashboard:
    build: ./dashboard
    depends_on: { db-central: { condition: service_healthy } }
    environment:
      DB_HOST: db-central
      DB_USER: ${DASHBOARD_DB_USER}
      DB_PASSWORD: ${DASHBOARD_DB_PASSWORD}
      DB_NAME: dashboard
      UNIFIED_READER_USER: unified_reader
      UNIFIED_READER_PASSWORD: ${UNIFIED_READER_PASSWORD}
      CHECKMK_URL: http://checkmk:5000/${CMK_SITE_ID}
      CHECKMK_USER: automation
      CHECKMK_SECRET: ${CMK_AUTOMATION_SECRET}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock

  checkmk:
    image: checkmk/check-mk-raw:latest
    # No usa MariaDB. RRDtool + SQLite internos.
    # El dashboard lo consulta via API y guarda en checkmk.host_snapshots.

  db-mirror:
    image: mariadb:11
    # Idéntico a db-central. Se llena con dumps cada 1h.

  db-sync:
    build: ./db-sync
    environment:
      DATABASES: passbolt dashboard checkmk mkmonitor
      SYNC_INTERVAL_HOURS: 1
```

---

## 10. Código ejemplo: Flask endpoint que consulta las 3 bases y devuelve JSON

`dashboard/unified.py` (resumido):

```python
import os
from sqlalchemy import create_engine, text

def _reader_engine():
    host = os.environ["DB_HOST"]
    user = os.environ["UNIFIED_READER_USER"]
    pwd  = os.environ["UNIFIED_READER_PASSWORD"]
    return create_engine(
        f"mysql+pymysql://{user}:{pwd}@{host}/?charset=utf8mb4",
        pool_pre_ping=True,
    )

def assets_health_summary():
    """Pega assets de mkmonitor + estado de checkmk + cuenta de creds en passbolt."""
    sql = text("""
        SELECT
            a.hostname, a.ip_address, a.type, a.criticality,
            cm.state_text  AS checkmk_state,
            (SELECT COUNT(*) FROM mkmonitor.incidents i
              WHERE i.asset_id = a.id AND i.status='open')      AS open_incidents,
            (SELECT COUNT(*) FROM passbolt.resources pb
              WHERE pb.deleted=0
                AND pb.uri LIKE CONCAT('%', a.hostname, '%'))   AS passbolt_resources
        FROM mkmonitor.assets a
        LEFT JOIN (
            SELECT host_name, state_text
            FROM checkmk.host_snapshots
            WHERE id IN (SELECT MAX(id) FROM checkmk.host_snapshots GROUP BY host_name)
        ) cm ON cm.host_name = a.hostname
        ORDER BY FIELD(a.criticality,'critical','high','medium','low'), a.hostname
    """)
    with _reader_engine().connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql)]
```

`dashboard/app.py`:

```python
@app.route("/api/unified")
@login_required
def api_unified():
    return {
        "overview":  unified.unified_overview(),
        "assets":    unified.assets_health_summary(),
        "incidents": unified.open_incidents_with_passbolt_creds(),
        "cross":     unified.cross_resources_to_hosts(),
    }
```

### Test rápido por curl

```bash
curl -s -c /tmp/c.txt -X POST http://localhost/login \
     -d "username=admin&password=$DASHBOARD_ADMIN_PASSWORD"
curl -s -b /tmp/c.txt http://localhost/api/unified | jq
```

### Vista HTML equivalente

http://localhost/unified — tabla con assets de MKMonitor + estado Checkmk + count de credenciales en Passbolt + incidentes abiertos + cruce de recursos. Todo en una sola página.

---

## Resumen ejecutivo

| Pregunta | Respuesta |
|---|---|
| ¿Cuántos motores? | **1** (MariaDB en `db-central`) |
| ¿Cuántas bases? | **4**: `passbolt`, `dashboard`, `checkmk`, `mkmonitor` |
| ¿Cómo lee la app agregadora? | Usuario `unified_reader` con SELECT en las 4 |
| ¿Escribe en passbolt/checkmk/mkmonitor? | **No**. Solo lectura desde el agregador |
| ¿Backup? | `bash scripts/backup.sh` — dump consistente con `--single-transaction` |
| ¿Réplica? | `db-mirror` actualizada por `db-sync` cada 1h |
| ¿Cómo cruza datos? | Joins cross-schema en MariaDB (`passbolt.resources` JOIN `checkmk.host_snapshots`) |
| ¿Vista web? | `/unified` (HTML) y `/api/unified` (JSON) |
