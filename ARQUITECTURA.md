# Arquitectura del proyecto

Como esta organizado el stack, que rol cumple cada pieza y por que hay
limites a lo que se puede "centralizar".

---

## Diagrama general

```
                      ┌────────────────────┐
                      │     Internet       │
                      └──────────┬─────────┘
                                 │ 80 / 443
                      ┌──────────▼─────────┐
                      │      nginx         │  reverse proxy + TLS
                      └─┬──────────┬────┬──┘
                        │          │    │
            ┌───────────┘          │    └──────────┐
            │                      │               │
            ▼                      ▼               ▼
      ┌──────────┐          ┌────────────┐   ┌──────────┐
      │ passbolt │          │  dashboard │   │ checkmk  │
      └────┬─────┘          └─┬───┬───┬──┘   └─────┬────┘
           │ MySQL            │   │   │ HTTPS API  │
           │                  │   │   └──────────┐ │
           │      docker.sock │   │              │ │ RRDtool +
           │      ┌───────────┘   │              │ │ SQLite
           │      ▼               │              │ │ (interno
           │  ┌──────┐            │              │ │  fijo)
           │  │Docker│            │              │ ▼
           │  │engine│            │              │ ┌──────────┐
           │  └──────┘            │              │ │/omd/sites│
           │                      ▼              │ │/monitor/ │
           ▼            ┌─────────────────┐      │ └──────────┘
   ┌──────────────────────────────────────┐     │
   │       db-central (MariaDB 11)        │◄────┘
   │   ┌──────────┐    ┌────────────┐     │   solo lectura
   │   │ passbolt │    │ dashboard  │     │
   │   └──────────┘    └────────────┘     │
   └──────────────┬───────────────────────┘
                  │ dump cada 2h (db-sync)
                  ▼
   ┌──────────────────────────────────────┐
   │        db-mirror (MariaDB 11)        │   espejo de seguridad
   │   ┌──────────┐    ┌────────────┐     │
   │   │ passbolt │    │ dashboard  │     │
   │   └──────────┘    └────────────┘     │
   └──────────────────────────────────────┘
```

---

## Que hace nginx en este proyecto

Nginx es la **unica puerta de entrada al stack**. Sin nginx, cada
contenedor tendria que exponer sus propios puertos al sistema operativo
y manejar su propio TLS — eso seria caotico y peligroso.

### Las 4 funciones concretas que cumple

**1. Punto unico de entrada (puertos 80 y 443)**

```
ANTES (sin nginx)              DESPUES (con nginx)
─────────────────              ───────────────────
:8000 → dashboard              :80  → nginx
:443  → passbolt               :443 → nginx (TLS)
:5000 → checkmk                       │
:3306 → mariadb                       ├─→ dashboard (interno)
                                      ├─→ passbolt (interno)
4 puertos abiertos             └─→ checkmk (interno)
en tu firewall                 Solo 2 puertos abiertos
```

**2. Enruta segun el dominio o ruta**

Cuando llega una peticion, nginx mira el `Host:` header y la ruta, y
decide a que contenedor mandarla:

| Lo que pide el navegador | Nginx lo manda a |
|---|---|
| `http://localhost/` | contenedor `dashboard:8000` |
| `http://localhost/login` | contenedor `dashboard:8000` |
| `https://passbolt.local/` | contenedor `passbolt:443` |
| `http://checkmk.local/` | contenedor `checkmk:5000` |

El usuario no se entera que hay 3 servicios distintos por detras.

**3. Centraliza el TLS (HTTPS)**

El certificado SSL vive solo en nginx. Los contenedores internos hablan
HTTP plano entre ellos (lo cual es seguro porque la red `backend` no
sale a internet).

```
Internet ──HTTPS──► nginx ──HTTP plano──► passbolt
              ▲
              └── certificado vive aqui,
                  no en cada servicio
```

**Beneficio:** cuando renueves el cert (Let's Encrypt cada 90 dias),
solo tocas nginx. Si cada app tuviera su propio cert serian 3 renovaciones.

**4. Aisla los servicios internos**

```
             ┌─────────── red "frontend" ────────────┐
             │                                       │
        nginx (publico, 80/443)                      │
             │                                       │
             ├──┬──────────────┬──────────────┐     │
             │  ↓              ↓              ↓      │
        passbolt          dashboard         checkmk  │
             │              │ │ │              │     │
             └──┴──────────────────────────────┘     │
                            ↓ ↓                      │
             ┌──────── red "backend" (privada) ──────┘
             │            ↓ ↓
             │       db-central, db-mirror
             │
             └─ NADIE de afuera puede llegar aqui
```

`db-central` y `db-mirror` solo viven en la red `backend`, que **no esta
publicada al host**. Si manana alguien encuentra un bug en Passbolt y
logra escapar del contenedor, todavia no puede tocar la DB directamente
desde internet.

### Resumen de nginx en una tabla

| Pregunta | Respuesta |
|---|---|
| ¿Es opcional? | No: sin el, los servicios necesitarian publicar sus propios puertos |
| ¿Procesa pedidos? | No: solo los redirige al contenedor correcto |
| ¿Donde vive el cert SSL? | Solo en nginx, no en passbolt ni en el dashboard |
| ¿Que pasa si lo apago? | El stack queda accesible solo desde dentro de docker — desde tu navegador no llegas a nada |

---

## Que tienes en la DB centralizada (db-central)

La DB central es **un solo proceso de MariaDB** que aloja **dos bases
separadas**, cada una propiedad de su aplicacion duena. No mezclamos
tablas: cada app tiene su schema y solo el las toca.

### Base 1: `passbolt` (la maneja Passbolt)

Aqui Passbolt guarda todo lo del producto. Tiene 30+ tablas, las mas
importantes:

| Tabla | Que contiene |
|---|---|
| `users` | Tus cuentas (nombre, email, llave PGP publica) |
| `resources` | Las "entradas" de contraseña (titulo, URL, usuario) |
| `secrets` | El password en si — **cifrado con la llave PGP del usuario** |
| `permissions` | Quien puede ver cada resource |
| `groups` / `groups_users` | Grupos y miembros |
| `comments` | Comentarios sobre cada resource |
| `folders` | Carpetas para organizar resources |
| `entities_history` | Auditoria interna de cambios |

> 🔐 **Importante:** los passwords en `secrets` estan cifrados de tal
> forma que ni siquiera con acceso completo a la DB puedes leerlos. Esto
> se explica mas abajo.

### Base 2: `dashboard` (la maneja el dashboard custom)

Aqui guarda lo que necesitamos nosotros:

| Tabla | Que contiene |
|---|---|
| `users` | Cuentas para entrar al panel custom (admin / usuario normal) |
| `monitored_targets` | Catalogo de cosas que queremos monitorear |
| `status_log` | Historial de chequeos de estado |
| `audit_log` | Bitacora: que admin presiono start/stop/restart en que contenedor y cuando |

### Por que las metimos juntas en un solo MariaDB

| Beneficio | Explicacion |
|---|---|
| **Un solo backup** | Un dump cubre las dos bases |
| **Un solo lugar para tunear** | Memoria, conexiones, indices — un set de parametros |
| **Menos contenedores** | Un solo `mariadbd` en vez de dos |
| **Mismo motor, misma version** | Evita inconsistencias entre Passbolt y dashboard |

---

## Que NO puede compartir Passbolt con la DB centralizada

Esta es la parte conceptual mas importante. Hay cosas que **estan en**
db-central pero que no son utiles ahi para nadie mas, y hay cosas que
**no pueden estar** ni siquiera teoricamente.

### Lo que esta cifrado y no se puede leer aunque tengas la DB

Las **contrasenas que guardas en Passbolt** estan en la tabla
`passbolt.secrets`, pero su valor es algo asi:

```
-----BEGIN PGP MESSAGE-----
hQEMAxYJpvuwh4yIAQf/XkN3pT6...muchas lineas de basura cifrada...
-----END PGP MESSAGE-----
```

Eso esta cifrado con la **llave publica PGP** de cada usuario. Para
descifrarlo se necesita la **llave privada**, y Passbolt **nunca** sube
esa llave privada al servidor — vive en el navegador del usuario, en
local.

**Consecuencia practica:** aunque tu logueas como root al MariaDB y
haces `SELECT * FROM passbolt.secrets`, solo ves el texto cifrado. No
puedes "centralizar" la lectura de contrasenas — fue diseñado asi para
proteger a los usuarios incluso del administrador del servidor.

```
Tu admin del SO  ──► tiene root MariaDB ──► ve la DB ──► ve basura cifrada
                                                         (no las pass)

Tu usuario normal ──► tiene su llave privada local ──► descifra solo lo suyo
```

### Lo que no se puede meter porque no tiene sentido

- **La sesion abierta del usuario** — Passbolt usa JWT firmados y la
  sesion vive en cookies del navegador, no en la DB
- **El estado del navegador** — selecciones, popups, configuracion
  visual — eso es local del browser
- **La llave privada PGP** — la genera y guarda solo el navegador

### Por que no se puede saltar la DB de Passbolt

Imagina que quieres "ahorrarte" la DB de Passbolt y escribir
contrasenas directo a `db-central.passbolt.secrets` desde otra app:

1. **Tendrias que cifrar tu mismo el password** con la llave PGP correcta del usuario destino — Passbolt hace ese trabajo, tu no.
2. **Tendrias que crear filas relacionadas** en `resources`, `permissions`, `secret_accesses`, `entities_history` — todas con FKs y validaciones.
3. **La proxima version de Passbolt cambia el schema** — tus inserts a mano dejarian la DB inconsistente y la migracion de la nueva version fallaria o corromperia datos.
4. **Passbolt no recargaria tus inserts** — su cache interna no sabe que algo aparecio sin que el lo escribiera.

**La regla:** la DB es propiedad **privada** de cada aplicacion. La forma
correcta de "compartir" es **compartir el motor de MariaDB**, no las tablas.

---

## Que NO puede compartir Checkmk con la DB centralizada

Caso totalmente distinto. Aqui el problema es el formato de los datos.

### Checkmk usa RRDtool, no MySQL

Cuando Checkmk recibe una metrica (CPU = 47.3%, mem = 1.2GB, ping = 8ms),
**no la escribe en una tabla de SQL**. La escribe en archivos `.rrd`
dentro del contenedor:

```
/omd/sites/monitor/var/check_mk/rrd/
  ├── localhost/
  │     ├── CPU_utilization.rrd
  │     ├── Memory.rrd
  │     ├── Disk_IO.rrd
  │     └── ...
  └── ...
```

RRDtool (Round-Robin Database) es un formato especializado para series
de tiempo. Es **mucho mas eficiente** que MySQL para guardar millones
de puntos de metricas, y maneja automaticamente:

- Compresion temporal (datos viejos se promedian: 1 punto/min se vuelve
  1 punto/hora despues de un dia)
- Retencion automatica (rota los datos viejos sin que tu hagas nada)
- Lectura ultrarrapida para graficar

### Por que no podemos cambiarlo a MySQL

| Razon | Explicacion |
|---|---|
| **No hay opcion oficial** | Checkmk no tiene un parametro `--use-mysql`. Su core esta atado a RRDtool y SQLite |
| **Modificar el codigo** | Habria que cambiar el codigo fuente de Checkmk. Cada update lo revertiria |
| **Performance** | RRDtool es 10–100x mas rapido que MySQL para time-series. Mover a SQL te haria mas lento al graficar |
| **Soporte** | Si rompes algo no tienes a quien preguntar; ya no usas Checkmk "oficial" |

### Como entonces "centralizamos" Checkmk

No movemos los datos — los **consultamos via API**. El dashboard
pregunta a Checkmk "¿como esta el host X?" usando su Web API REST, y
muestra la respuesta en la misma pantalla donde se ven los datos de
db-central.

```
Dashboard ──HTTPS GET──► Checkmk API
                              │
                              ▼
                         RRDtool/SQLite
                         (privado de Checkmk,
                          no entra a db-central)
```

Esto es **federacion**: cada app sigue dueña de sus datos, pero un
agregador (el dashboard) los muestra unificados.

---

## La DB espejo (db-mirror)

`db-mirror` es una **copia de db-central refrescada cada 2 horas** que
vive en otro contenedor y otro volumen. Esto te lo hace el servicio
`db-sync`.

### Como funciona el sync

```
cada 2h                    db-sync ejecuta:

   ┌────────┐          1. mariadb-dump db-central
   │db-     │   dump      --databases passbolt dashboard
   │central │ ─────────►  --add-drop-database
   └────────┘
                       2. pipe el dump a:
                          mariadb db-mirror

   ┌────────┐          3. db-mirror queda con
   │db-     │ ◄─restore   las dos bases reescritas
   │mirror  │             desde cero
   └────────┘

   El primer sync corre al arrancar el stack (no espera 2h).
   Si el sync falla, el mirror queda con la version de hace 2h previa.
```

### Para que sirve

| Caso de uso | Como ayuda el mirror |
|---|---|
| **db-central se corrompe / borra accidental** | Tienes una copia menos de 2h vieja en linea, sin restaurar de un dump en disco |
| **Reportes pesados** | Lees del mirror — no afectas el rendimiento de Passbolt en vivo |
| **Backups en disco** | El dump nocturno puede tomarse del mirror sin lockear nada |
| **Debugging** | Puedes hacer queries destructivas contra el mirror sin miedo |

### Limitaciones

- **No es replicacion en vivo.** Hay un retraso de hasta 2h entre lo que
  cambias en Passbolt y lo que ves en el mirror.
- **Es solo lectura recomendada.** Si escribes ahi, el proximo sync
  borra tus cambios.
- **Solo replica las dos bases SQL** (`passbolt`, `dashboard`). NO
  incluye los datos de Checkmk (que viven en RRDtool, no en SQL).

### Como verlo desde el dashboard

Vas a `http://localhost/mirror` y ves:
- Cuando fue el ultimo sync (timestamp + cuanto tardo)
- Si esta `OK` o `FAIL`
- Las tablas espejadas con conteo de filas

---

## Tabla resumen

| Componente | Que guarda y donde | Compartido con db-central |
|---|---|---|
| Passbolt | passwords cifrados, usuarios, permisos en `db-central.passbolt` | ✅ Si (motor compartido) |
| Dashboard custom | usuarios, audit log en `db-central.dashboard` | ✅ Si |
| Checkmk | metricas RRDtool en su volumen propio | ❌ No (formato distinto) |
| db-mirror | copia de las dos bases SQL cada 2h | (es la copia) |
| nginx | nada — solo enruta peticiones | (no usa DB) |

---

## Credenciales

Listadas en [`INICIO.md`](INICIO.md), seccion 5 (Acceder a las webs).
