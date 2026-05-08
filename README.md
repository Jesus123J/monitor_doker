# Stack Passbolt + Checkmk + Dashboard centralizado

Proyecto en Docker Compose con:

- **db-central** — MariaDB unica que aloja `passbolt` y `dashboard`.
- **passbolt** — gestor de contrasenas (CE), conectado a la DB central.
- **checkmk** — monitoreo de servidores. Mantiene su storage interno (RRDtool) por diseno; el dashboard lo consulta via Web API.
- **dashboard** — web custom en Flask con **registro/login de usuarios** que muestra el estado de Passbolt, Checkmk, la DB y todos los contenedores.
- **nginx** — reverse proxy delante de todo.

```
docker-compose.yml
.env.example
db/init/01-init-databases.sh        <- crea base "dashboard" + usuario
dashboard/                          <- app Flask (auth + monitor)
nginx/nginx.conf
```

## 1. Configurar variables

```bash
cp .env.example .env
# edita .env y cambia TODOS los passwords
```

## 2. Levantar el stack

```bash
docker compose up -d
```

Espera a que `db-central` quede `healthy` (el resto depende de el).

## 3. Crear el usuario "automation" en Checkmk

El dashboard usa la Web API de Checkmk para listar hosts. Hay que crear el usuario una sola vez:

1. Abre `http://checkmk.local/` (o `http://localhost:5000/monitor` segun como expongas Checkmk).
2. Login: `cmkadmin` / el `CMK_PASSWORD` que pusiste en `.env`.
3. Ve a **Setup → Users → Add user**.
4. Username: `automation`, Roles: `Administrator`. Genera el secret.
5. Copia el secret al `.env` en `CMK_AUTOMATION_SECRET` y reinicia el dashboard:
   ```bash
   docker compose up -d dashboard
   ```

## 4. Configurar Passbolt

Primer arranque de Passbolt necesita crear el usuario admin:

```bash
docker compose exec passbolt su -m -c \
  "/usr/share/php/passbolt/bin/cake passbolt register_user \
   -u admin@example.com -f Admin -l User -r admin" \
  -s /bin/sh www-data
```

Toma el link que imprime y completa el registro en el navegador.

## 5. Entrar al dashboard custom

- `http://localhost/` → registro/login del dashboard.
- El **primer usuario que se registra queda como admin**.
- Una vez dentro veras:
  - Tarjetas de estado de Passbolt, Checkmk y la DB central.
  - Tabla con todos los contenedores (running / exited / health).

## Esquema de la DB central

```
db-central (MariaDB 11)
├── passbolt        (la usa Passbolt)
└── dashboard
    ├── users               <- registro/login
    ├── monitored_targets   <- catalogo de cosas a monitorear
    └── status_log          <- historico de chequeos
```

## Por que Checkmk no comparte la DB

Checkmk no soporta MySQL/MariaDB para sus metricas (usa RRDtool + SQLite por sitio).
El dashboard cubre el "todo en un solo lugar" consultando su Web API y guardando
los resultados en `dashboard.status_log` si quieres historizar.

## Generar certs autofirmados para Passbolt (dev)

```bash
mkdir -p nginx/certs
openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
  -keyout nginx/certs/passbolt.key \
  -out    nginx/certs/passbolt.crt \
  -subj "/CN=passbolt.local"
```

Y agrega a `/etc/hosts` (o `C:\Windows\System32\drivers\etc\hosts`):

```
127.0.0.1 passbolt.local checkmk.local
```
