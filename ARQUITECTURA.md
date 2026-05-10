# Como se conecta todo

Este documento explica por que el stack esta organizado como esta, que rol cumple
cada componente, donde guarda sus datos y por que **no podemos** simplemente
"saltarnos" la base de datos propia de cada aplicacion.

---

## Diagrama de conexiones

```
                  ┌────────────────────┐
                  │     Internet       │
                  └──────────┬─────────┘
                             │ 80/443
                  ┌──────────▼─────────┐
                  │      nginx         │   reverse proxy + TLS
                  └─┬──────────┬────┬──┘
                    │          │    │
        ┌───────────┘          │    └──────────┐
        │                      │               │
        ▼                      ▼               ▼
  ┌──────────┐          ┌────────────┐   ┌──────────┐
  │ passbolt │          │  dashboard │   │ checkmk  │
  │   (CE)   │          │   (Flask)  │   │   (raw)  │
  └────┬─────┘          └─┬───┬───┬──┘   └─────┬────┘
       │ MySQL            │   │   │ HTTPS API  │
       │ protocolo        │   │   │            │
       │                  │   │   └─────────┐  │
       │   ┌──────────────┘   │             │  │
       │   │ docker.sock      │             │  │
       │   │ (RW)             │ MySQL       │  │ RRDtool +
       │   ▼                  │ protocolo   │  │ SQLite
       │ ┌─────┐              │             │  │ (interno)
       │ │Docker│              │             │  ▼
       │ │engine│              │             │ ┌──────────┐
       │ └─────┘              │             │ │ /omd/    │
       │                      │             │ │ sites/   │
       ▼                      ▼             │ │ monitor/ │
  ┌──────────────────────────────┐          │ └──────────┘
  │   db-central (MariaDB 11)    │          │
  │  ┌──────────┐  ┌───────────┐ │◄─────────┘
  │  │ passbolt │  │ dashboard │ │  consulta
  │  └──────────┘  └───────────┘ │
  └──────────┬───────────────────┘
             │ dump cada 2h
             ▼
  ┌──────────────────────────────┐
  │   db-mirror (MariaDB 11)     │   espejo de seguridad
  │  ┌──────────┐  ┌───────────┐ │   (snapshot, no en vivo)
  │  │ passbolt │  │ dashboard │ │
  │  └──────────┘  └───────────┘ │
  └──────────────────────────────┘
```

---

## Cada componente y su base de datos

| Componente | DB que necesita por diseno | Donde la pusimos |
|---|---|---|
| **Passbolt** | MySQL/MariaDB (oficial) | `db-central` -> schema `passbolt` |
| **Dashboard** | Cualquier SQL | `db-central` -> schema `dashboard` |
| **Checkmk** | RRDtool + SQLite (interno, no opcional) | Volumen `checkmk_sites` |
| **Mirror** | MariaDB | `db-mirror` (refrescado cada 2h) |

### Por que cada app tiene su propia "DB" tecnica

Cada aplicacion fue programada con SU modelo de datos propio:

- **Passbolt** tiene 30+ tablas (users, resources, secrets, permissions,
  comments, folders, groups, group_users, etc.)
- **Checkmk** tiene archivos RRD por host + ficheros de configuracion `.mk` +
  una SQLite para estado de la UI
- **Dashboard** tiene 4 tablas (users, monitored_targets, status_log, audit_log)

Cuando una app arranca, hace **migraciones**: crea sus tablas, agrega columnas
nuevas, cambia tipos. Esto es interno de cada producto. Si tu cambias
manualmente esas tablas, la proxima version romperia todo.

---

## Que datos guardamos en la DB central y cuales NO

### Si guardamos en la DB central

- **Todo lo de Passbolt:** cuentas, contrasenas (cifradas), grupos, permisos,
  comentarios, carpetas, sesiones, llaves PGP publicas, etc.
- **Todo lo del dashboard:** usuarios del panel, bitacora de acciones
  operativas (audit_log), catalogo de objetivos monitoreados.

### NO guardamos en la DB central

- **Metricas de Checkmk:** uso de CPU, memoria, disco, red, ping, alarmas.
  Eso vive en RRDtool dentro del contenedor `checkmk`.
- **Llaves privadas PGP:** Passbolt es **zero-knowledge**. Las llaves privadas
  jamas tocan el servidor — viven en el navegador del usuario. Por eso
  aunque tengas acceso completo a la DB, no puedes leer las contrasenas que
  guardan los usuarios.

---

## Por que Checkmk no puede usar la DB central

Esta es la pregunta tecnica mas importante del proyecto.

**Checkmk fue disenado para usar RRDtool**, no SQL. Razones:

1. **Performance.** Las metricas de monitoreo son *time-series*: miles de
   puntos por hora por host. RRDtool guarda esto 10 a 100 veces mas eficiente
   que MySQL, y consulta promedios/maximos historicos en milisegundos.

2. **Round-robin.** RRDtool *automaticamente* va degradando la resolucion de
   datos viejos (manda 1 punto/min los datos del ultimo dia, 1 punto/hora los
   de la ultima semana, 1 punto/dia los del ultimo mes). MySQL no hace eso solo.

3. **No hay opcion oficial.** Checkmk no expone un parametro
   `--use-mysql-instead`. Cambiar esto requeriria modificar el codigo fuente,
   y cada actualizacion volveria a romper tu cambio.

4. **No vale la pena.** Checkmk ya consume en disco unos cientos de KB por
   host por mes. La centralizacion no aporta nada operativamente: el
   storage de Checkmk se respalda con su volumen, no con un dump de SQL.

**Como "centralizamos" entonces?** El dashboard consulta la **API REST de
Checkmk** y muestra el estado actual junto con todo lo demas. El usuario
ve un solo panel, aunque por dentro haya dos bases distintas.

---

## Por que NO se puede saltar la DB de cada app

Imagina que quisieramos *bypasear* la DB de Passbolt y escribir directo
en `db-central.passbolt`. Pasaria esto:

1. **Romperias el cifrado.** Las contrasenas en Passbolt se cifran con la
   llave PGP del usuario *antes* de tocar la DB. Si insertas un texto plano,
   Passbolt lo veria como dato corrupto y/o lo expondria.

2. **Romperias las relaciones.** Una contrasena en Passbolt vive en la tabla
   `secrets`, pero ademas necesita filas en `resources`, `permissions`,
   `secret_accesses`, `entities_history`, etc. Saltearte la app significa
   reimplementar toda esa logica.

3. **Romperias las migraciones.** La proxima version de Passbolt (4.x ->
   4.y) trae cambios de schema. Si tu modificaste cosas a mano, el
   migrador o falla o corrompe los datos.

4. **Nadie lee tus datos despues.** La app no sabe leer "datos que escribio
   alguien que no es ella". Si pones contrasenas a mano en la DB, en la UI de
   Passbolt no aparecen.

**La regla:** la DB es propiedad **privada** de cada aplicacion. Centralizar
significa **compartir el motor** (la instancia de MariaDB), no las tablas.

---

## Como SI centralizamos correctamente

**1. Compartir el motor de DB.**

```
        ┌────────────┐
        │ db-central │  un solo MariaDB
        │            │
        │ ┌────────┐ │  schema "passbolt"  -> dueno: Passbolt
        │ │passbolt│ │
        │ └────────┘ │
        │ ┌────────┐ │  schema "dashboard" -> dueno: Dashboard
        │ │dashbord│ │
        │ └────────┘ │
        └────────────┘
```

Cada app tiene su schema, pero el proceso `mariadbd` es uno solo. Beneficios:

- Un solo backup
- Un solo punto de tuning de performance
- Un solo lugar para monitorear

**2. Federacion via API para apps que no soportan SQL externo.**

```
  Dashboard ────HTTPS API────► Checkmk
                                   │
                                   ▼
                              RRDtool/SQLite
                              (privado de Checkmk)
```

El dashboard pregunta "dame el estado de los hosts" via API REST de Checkmk,
y muestra la respuesta junto con los demas servicios.

**3. Mirror para snapshots periodicos.**

```
  db-central ──cada 2h: dump+restore──► db-mirror
```

`db-mirror` es una copia exacta de `db-central` cada 2 horas. Sirve para:

- Recuperacion ante desastres (si `db-central` se corrompe)
- Reportes pesados que no afectan a Passbolt en vivo
- Punto de partida para backups en disco

---

## Credenciales (todos los passwords del sistema)

Estos son los passwords activos. Estan en `.env` (que NO se sube al repo).

| Para que sirve | Usuario | Password |
|---|---|---|
| Dashboard (web custom) | `admin` | `6b8403e319ca019a` |
| Checkmk (UI y API) | `cmkadmin` | `d78448435920c084916b057e` |
| Checkmk automation API | `automation` | `892910c464e4b6ebeebdf0f50356f3f3` |
| MariaDB root (db-central y db-mirror) | `root` | `ff774dde3d011806bd102ec3d6cd3cad` |
| MariaDB user de Passbolt | `passbolt` | `8137683410f69dda72742b1f30e288c9` |
| MariaDB user del dashboard | `dashboard` | `10a5531c02fb1ebb22e2bb40c2a46269` |
| Flask SECRET_KEY (firma cookies) | — | `b6633da1...` (en .env) |

Passbolt tiene su propia logica de login: tu **passphrase** + tu **llave
privada PGP** descargada en el navegador. NO hay un "password de admin de
Passbolt" en el servidor — esa es justamente la garantia zero-knowledge.

---

## URLs y a que sirven

| URL | Para que |
|---|---|
| `http://localhost/` | Dashboard custom (login admin) |
| `https://localhost:8443/` | Passbolt (gestor de contrasenas) |
| `http://localhost:5050/monitor/` | Checkmk (monitoreo) |

Todas las URLs internas (`db-central:3306`, `passbolt:443`, etc.) **no son
accesibles desde tu host**, solo entre contenedores. Esa es la idea de la
red `backend` privada.

---

## TL;DR

- **Passbolt + Dashboard** → DB central (porque ambos hablan MySQL).
- **Checkmk** → su propia DB interna, NO se puede mover (decision tecnica del producto).
- **db-mirror** → snapshot de la DB central cada 2h.
- **No se puede** saltar la DB propia de cada app porque cada app es la unica
  que sabe leer/escribir su propio formato. Centralizar = compartir motor,
  no tablas.
