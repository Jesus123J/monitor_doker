# Donde guarda informacion cada app

Guia practica de **donde** persiste cada cosa el stack, y **como** ver esa
informacion para auditoria, debug o reportes.

---

## Resumen ultra rapido

| App | Que guarda | Donde |
|---|---|---|
| **Passbolt** | Usuarios, contrasenas cifradas, permisos, accesos, audit interna | `db-central.passbolt` (35 tablas) |
| **Dashboard** | Cuentas del panel, bitacora de acciones, ciclo de vida de contenedores | `db-central.dashboard` (4 tablas) |
| **Checkmk** | Metricas time-series, configuracion del sitio, log de eventos | Volumen `checkmk_sites` (RRDtool + SQLite + ficheros .mk) |

> Passbolt y Dashboard comparten motor MariaDB. Checkmk usa su propio storage
> interno por diseno (RRDtool no es SQL).

---

## 1. Passbolt — base `db-central.passbolt`

### Tablas principales y que contienen

| Tabla | Filas tipicas | Que guarda |
|---|---|---|
| `users` | tus usuarios | id, email, role_id, active, deleted, created |
| `profiles` | uno por usuario | nombre completo, fecha de nacimiento, etc. |
| `roles` | 3 fijas | admin, user, guest |
| `gpgkeys` | uno por usuario | llave **publica** PGP en formato ASCII |
| `resources` | una por contrasena guardada | metadatos (nombre, URL, usuario) — **NO el password** |
| `secrets` | una por contrasena | el password cifrado con PGP — ilegible sin llave privada |
| `secrets_history` | una por revision | versiones anteriores del secret cuando se edita |
| `permissions` | varias por recurso | quien puede ver/editar cada resource (user_id, resource_id, type) |
| `groups` | grupos de equipo | nombre del grupo |
| `groups_users` | n-a-n | membresias |
| `folders` | carpetas para organizar | id, name, owner |
| `comments` | comentarios sobre recursos | id, content, user_id |
| `favorites` | marcado como favorito | user_id, resource_id |

### Tablas de auditoria de Passbolt

Passbolt tiene **su propia bitacora interna** ademas de lo nuestro:

| Tabla | Que guarda |
|---|---|
| `action_logs` | Cada accion realizada: create user, read secret, update password... |
| `actions` | Catalogo de tipos de accion (mapeo) |
| `entities_history` | Cambios sobre entidades (insert/update/delete) |
| `permissions_history` | Cuando se cambio el permiso de un recurso |
| `folders_history` / `folders_relations_history` | Cambios sobre carpetas |
| `email_queue` | Mails pendientes/enviados (verificacion de cuenta, etc.) |

### Tablas tecnicas / cache

| Tabla | Que guarda |
|---|---|
| `authentication_tokens` | Tokens de sesion / refresh — uno por login activo |
| `avatars` | Avatares de los usuarios |
| `phinxlog` | Migraciones aplicadas (no tocar) |
| `rbacs` | Reglas de control de acceso por rol |
| `account_settings` | Settings por usuario (idioma, tema, etc.) |
| `organization_settings` | Settings globales (smtp, sso, etc.) |

### Como inspeccionar

Desde la UI (admin):
- **http://localhost/db-central** → tab "DB Central" → schema `passbolt` → click "Ver filas" sobre cualquier tabla

Por consola:
```bash
docker exec -it db-central mariadb -uroot -p"$DB_ROOT_PASSWORD" passbolt
> SHOW TABLES;
> SELECT username, active, deleted FROM users;
> SELECT id, name, username, uri FROM resources WHERE deleted=0;
> SELECT created, user_id, action_id FROM action_logs ORDER BY created DESC LIMIT 20;
```

### Ejemplo: ver quien hizo que y cuando

```sql
SELECT
    al.created                       AS cuando,
    u.username                       AS quien,
    a.name                           AS que_hizo,
    al.status                        AS resultado
FROM passbolt.action_logs al
JOIN passbolt.actions     a ON a.id = al.action_id
JOIN passbolt.users       u ON u.id = al.user_id
ORDER BY al.created DESC
LIMIT 30;
```

---

## 2. Dashboard custom — base `db-central.dashboard`

| Tabla | Para que sirve |
|---|---|
| `users` | Cuentas para entrar al panel (`admin`, etc.) — separadas de Passbolt |
| `monitored_targets` | Catalogo de "que cosas estamos monitoreando" |
| `status_log` | Historial periodico de chequeos |
| `audit_log` | **Bitacora de acciones operativas** (start/stop/restart de contenedores, sync manual del mirror, gestion de usuarios) |
| `container_lifecycle` | Cada transicion de estado de contenedor (running -> exited, etc.) |

### Como verlo desde la UI

| Pagina | Que muestra |
|---|---|
| `/users` (admin) | Usuarios del panel |
| `/audit` (admin) | Las ultimas 100 acciones operativas |
| `/lifecycle` | Resumen de uptime/downtime + ultimos 50 eventos |
| `/db-central` (admin) | Cualquier tabla con sus filas |
| `/schema` (admin) | Estructura (columnas) de cada tabla |

### ⚠️ Sí, las caidas y reinicios SI se guardan

Cada vez que un contenedor cambia de estado, el worker `lifecycle.py`
inserta una fila en `dashboard.container_lifecycle`. Es persistente —
queda en la DB para siempre (hasta que la borres) y se replica al mirror
en el proximo sync.

**Esquema de la tabla:**

| Columna | Que es |
|---|---|
| `id` | Auto incrementa |
| `container_name` | Nombre del contenedor |
| `prev_state` | Estado anterior (`running`, `exited`, `restarting`, `paused`, NULL si es primer registro) |
| `new_state` | Estado nuevo |
| `health` | Resultado del healthcheck (`healthy`, `unhealthy`, `starting`, `n/a`) |
| `duration_seconds` | Cuanto tiempo duro el estado **anterior** |
| `detail` | Texto descriptivo de la transicion |
| `created_at` | Fecha y hora del evento (UTC) |

**Queries listas para auditoria:**

```sql
-- Todas las caidas (exited) en los ultimos 7 dias
SELECT container_name, created_at, duration_seconds, detail
FROM dashboard.container_lifecycle
WHERE new_state = 'exited'
  AND created_at > NOW() - INTERVAL 7 DAY
ORDER BY created_at DESC;

-- Cuanto tiempo total estuvo apagado cada contenedor en la ultima semana
SELECT container_name,
       SUM(duration_seconds) AS segundos_apagado
FROM dashboard.container_lifecycle
WHERE prev_state = 'exited'
  AND created_at > NOW() - INTERVAL 7 DAY
GROUP BY container_name
ORDER BY segundos_apagado DESC;

-- Contenedores que mas se reiniciaron
SELECT container_name, COUNT(*) AS reinicios
FROM dashboard.container_lifecycle
WHERE prev_state = 'exited' AND new_state = 'running'
GROUP BY container_name
ORDER BY reinicios DESC;

-- Transiciones a unhealthy (problemas de salud)
SELECT container_name, created_at, detail
FROM dashboard.container_lifecycle
WHERE detail LIKE 'health % -> unhealthy'
ORDER BY created_at DESC;
```

**Desde la UI sin escribir SQL:** ir a `/lifecycle` muestra exactamente lo
mismo en formato visual: uptime / downtime por contenedor + timeline de
eventos.

---

## 3. Checkmk — NO usa MariaDB

Checkmk **no guarda nada en `db-central`**. Sus datos viven en el volumen
`checkmk_sites` montado en `/omd/sites/monitor/` dentro del contenedor.

### Que guarda y donde

| Tipo de dato | Ubicacion | Formato |
|---|---|---|
| **Metricas de monitoreo** (CPU, mem, ping, disk) | `/omd/sites/monitor/var/check_mk/rrd/<host>/<metric>.rrd` | RRDtool (binario time-series) |
| **Configuracion del sitio** (hosts, checks, reglas) | `/omd/sites/monitor/etc/check_mk/*.mk` | Texto Python |
| **Log de eventos** del nucleo | `/omd/sites/monitor/var/log/web.log`, `cmc.log` | Texto |
| **Notificaciones** | `/omd/sites/monitor/var/check_mk/notify/notify.log` | Texto |
| **Auditoria de cambios** | `/omd/sites/monitor/var/log/wato/audit.log` | Texto plano |
| **Usuarios** | `/omd/sites/monitor/etc/htpasswd` + `etc/check_mk/multisite.d/wato/users.mk` | Mixto |

### Como ver los logs de Checkmk

```bash
# Ver logs del nucleo (errores, problemas):
docker exec checkmk cat /omd/sites/monitor/var/log/cmc.log | tail -50

# Auditoria de cambios de configuracion:
docker exec checkmk cat /omd/sites/monitor/var/log/wato/audit.log | tail -30

# Notificaciones enviadas (cuando algo falla):
docker exec checkmk cat /omd/sites/monitor/var/check_mk/notify/notify.log | tail -30

# Estado actual de todos los hosts y sus servicios:
docker exec checkmk omd su monitor -c "cmk -l"
```

### Acceder via Web API (lo que hace nuestro dashboard)

```bash
# Hosts
curl -H "Authorization: Bearer automation $CMK_AUTOMATION_SECRET" \
     http://localhost:5050/monitor/check_mk/api/1.0/domain-types/host/collections/all

# Servicios en estado problema
curl -H "Authorization: Bearer automation $CMK_AUTOMATION_SECRET" \
     "http://localhost:5050/monitor/check_mk/api/1.0/domain-types/service/collections/all?query=%7B%22op%22%3A%22%3D%22%2C%22left%22%3A%22service_state%22%2C%22right%22%3A%222%22%7D"
```

---

## 4. Que pasa si Checkmk detecta un problema (host lento, caido)

### Niveles de problema en Checkmk

Cada host y cada chequeo (CPU, ping, disk) tiene un **estado**:

| Estado | Significado | Codigo |
|---|---|---|
| OK | Todo bien | 0 |
| WARN | Cerca del limite (ej. CPU 80%) | 1 |
| CRIT | Critico (ej. CPU 95%, host no responde) | 2 |
| UNKNOWN | No se pudo evaluar | 3 |

### Que sucede automaticamente

1. **Checkmk lo refleja en su UI** en tiempo real (cada 1 minuto rechequea).
2. **Si esta configurado, manda notificacion** (mail/Slack/SMS/webhook). Por defecto **no hay notificaciones configuradas** en este stack — hay que crear reglas en *Setup → Notifications*.
3. **Se persiste en el log** de eventos (`cmc.log`) y en el contador interno.
4. **El dashboard custom muestra** el conteo de hosts via API en `/` (la tarjeta "Checkmk" del header) — actualmente muestra solo cuantos hosts hay; podriamos extenderlo para mostrar cuantos estan en WARN o CRIT.

### Que hacemos nosotros cuando algo falla

Ahora mismo el flujo es manual:

1. Vamos a **http://localhost:5050/monitor/** → Monitor → All hosts → ver estado
2. Si hay rojo, click → ver que servicio falla → leer detalle
3. Vamos al contenedor afectado en el dashboard → ver logs → diagnostico

### Mejoras posibles (no implementadas todavia)

- Pull de problemas Checkmk a nuestra pagina `/problems` (issue futuro)
- Notificaciones por mail integradas (issue [#8](https://github.com/Jesus123J/monitor_doker/issues/8) ya abierto)
- Alertas en tiempo real con WebSocket en el dashboard

---

## 5. Como detectar si la auditoria esta funcionando

| Comprobacion | Como |
|---|---|
| ¿Passbolt registra accesos? | `SELECT COUNT(*) FROM passbolt.action_logs;` — deberia crecer con cada login/save |
| ¿Mi dashboard registra acciones? | Abrir `/audit` despues de hacer start/stop de un contenedor — deberia aparecer la fila |
| ¿Lifecycle tracker funciona? | Abrir `/lifecycle` — deberia mostrar al menos las transiciones del propio arranque |
| ¿Checkmk loguea? | `docker exec checkmk cat /omd/sites/monitor/var/log/wato/audit.log` |
| ¿Las notificaciones de Checkmk se mandan? | `docker exec checkmk cat /omd/sites/monitor/var/check_mk/notify/notify.log` (si no hay reglas, sale vacio) |

---

## 6. Recordatorio: que NO esta en la DB central

Pongo esto en bold porque genera confusion:

- ❌ **El password real (descifrado)** de Passbolt — vive solo en tu navegador despues de descifrarlo con tu passphrase + llave privada.
- ❌ **Tu passphrase de Passbolt** — nunca toca el servidor.
- ❌ **Tu llave privada PGP** — vive en el navegador.
- ❌ **Las metricas de Checkmk** (CPU, mem, etc.) — viven en RRDtool, no en MySQL.
- ❌ **La sesion de tu navegador** — cookies + tokens, no DB.
