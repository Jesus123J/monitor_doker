# Guia de inicio rapido

Si acabas de clonar el repo, **leeme primero**.

> Para entender *como* funciona el proyecto por dentro, mira [`ARQUITECTURA.md`](ARQUITECTURA.md).
> Para desplegar en un server, mira [`DEPLOY.md`](DEPLOY.md).

---

## 1. Que necesitas tener instalado

| Herramienta | Para que | Como verificar |
|---|---|---|
| **Docker Engine** ≥ 24 | Correr los contenedores | `docker --version` |
| **Docker Compose v2** | Orquestar el stack | `docker compose version` |
| **git** | Clonar el repo | `git --version` |
| **openssl** | Generar certs SSL self-signed | `openssl version` |
| **bash** | Correr el script de setup | (linux/mac nativo, Windows: Git Bash) |

En Windows lo mas facil es instalar **Docker Desktop** — ya viene con todo.

---

## 2. Primer arranque (3 comandos)

```bash
git clone https://github.com/Jesus123J/monitor_doker.git
cd monitor_doker

# Genera certs SSL y crea .env si no existe
bash scripts/setup.sh

# Levanta los 7 contenedores
docker compose up -d
```

La primera vez tarda 5–10 minutos descargando imagenes (MariaDB, Passbolt, Checkmk, etc.).

---

## 3. Variables de entorno (`.env`)

El archivo `.env` se crea automatico copiando `.env.example`. Estas son sus variables:

| Variable | Para que |
|---|---|
| `DB_ROOT_PASSWORD` | Password de root de MariaDB (tanto en `db-central` como en `db-mirror`) |
| `PASSBOLT_DB_USER` / `PASSBOLT_DB_PASSWORD` | Usuario MySQL que usa Passbolt para conectarse |
| `DASHBOARD_DB_USER` / `DASHBOARD_DB_PASSWORD` | Usuario MySQL que usa el dashboard custom |
| `PASSBOLT_DOMAIN` | Hostname/puerto donde Passbolt va a estar publicado (afecta los links de registro) |
| `CMK_SITE_ID` | ID interno del sitio de Checkmk (default: `monitor`) |
| `CMK_PASSWORD` | Password de `cmkadmin` (login a la UI de Checkmk) |
| `CMK_AUTOMATION_SECRET` | Secret del usuario `automation` para que el dashboard consulte la API de Checkmk |
| `DASHBOARD_SECRET_KEY` | Llave para firmar las cookies de sesion de Flask |
| `DASHBOARD_ADMIN_USER` / `DASHBOARD_ADMIN_EMAIL` / `DASHBOARD_ADMIN_PASSWORD` | Cuenta admin del dashboard que se crea automaticamente al primer arranque |

> 🔐 Para uso local los passwords del `.env.example` ya estan generados. Para
> server publico, **regenera todos** con `openssl rand -hex 32`.

---

## 4. Verificar que arranco bien

```bash
docker compose ps
```

Tienes que ver los **7 contenedores** en estado `Up (healthy)`:

| Contenedor | Para que sirve |
|---|---|
| `db-central` | Base de datos MariaDB principal (passbolt + dashboard) |
| `db-mirror` | Espejo de db-central, refrescado cada 2h |
| `db-sync` | Worker que copia db-central -> db-mirror |
| `passbolt` | Gestor de contrasenas |
| `checkmk` | Monitoreo de servidores |
| `dashboard` | Panel web custom (login + monitoreo unificado) |
| `nginx` | Reverse proxy (puertos 80 y 443) |

Si alguno aparece `unhealthy` o reiniciandose, mira sus logs:

```bash
docker compose logs <nombre>          # ver logs
docker compose logs -f <nombre>       # ver en vivo
```

---

## 5. Acceder a las webs

Despues del primer `docker compose up -d`:

| Servicio | URL | Login inicial |
|---|---|---|
| 🏠 **Dashboard** | http://localhost/ | `admin` / `6b8403e319ca019a` |
| 🔐 **Passbolt** | https://localhost:8443/ | (ver paso 6) |
| 🖥️ **Checkmk** | http://localhost:5050/monitor/ | `cmkadmin` / `d78448435920c084916b057e` |

> ⚠️ Cambia las claves de `.env` antes de hacer esto publico. Las que vienen
> son **passwords compartidos**, perfectos para desarrollo, peligrosos para produccion.

---

## 6. Configuracion inicial obligatoria (post-arranque)

### Passbolt: registrar el admin

Passbolt usa cifrado E2E con PGP, asi que el admin se registra con un comando:

```bash
docker compose exec -u www-data passbolt /usr/share/php/passbolt/bin/cake \
  passbolt register_user -u tu-correo@ejemplo.com -f Tu -l Nombre -r admin
```

Te imprime un link `https://localhost:8443/setup/start/.../...` — abrelo en el
navegador, instala la extension de Passbolt y completa el registro creando tu
**passphrase** (es la llave maestra).

### Checkmk: crear el usuario `automation`

Para que el dashboard pueda consultar la API de Checkmk:

1. Entra a http://localhost:5050/monitor/ con `cmkadmin` / el password de `.env`
2. **Setup → Users → Add user**
3. Username: `automation`, Roles: `Administrator`
4. Genera el "automation secret" y copialo
5. Pegalo en `.env` en la linea `CMK_AUTOMATION_SECRET=...`
6. Reinicia el dashboard:
   ```bash
   docker compose up -d dashboard
   ```

---

## 7. Encender y apagar contenedores

### Desde la terminal

```bash
# Levantar todo
docker compose up -d

# Apagar todo (mantiene datos)
docker compose down

# Apagar todo + BORRAR todos los datos (cuidado!)
docker compose down -v

# Apagar / encender un servicio especifico
docker compose stop passbolt
docker compose start passbolt

# Reiniciar un servicio
docker compose restart dashboard

# Reconstruir un servicio (despues de cambiar codigo)
docker compose up -d --build dashboard
```

### Desde el dashboard (modo experto)

Si tu usuario es **admin**, puedes encender/apagar/reiniciar contenedores
desde la UI sin tocar la terminal:

1. Login en http://localhost/ como admin
2. Click en **"Detalle"** de cualquier contenedor
3. Aparece la seccion amarilla **"Acciones (admin)"** con:
   - 🟢 **Start**
   - 🔴 **Stop** (pide confirmacion)
   - 🟡 **Restart** (pide confirmacion)

Cada accion queda registrada en `/audit` con: usuario, fecha, accion, target, resultado.

---

## 8. Quien puede entrar al dashboard

| Tipo | Que puede hacer |
|---|---|
| **Visitante** | Solo `/login` y `/register` |
| **Usuario normal** | Inicio, /problems, /container/&lt;name&gt;, /mirror |
| **Admin** | Todo lo anterior **+** start/stop/restart, /users, /schema, /audit |

### Crear nuevos usuarios

1. **Auto-registro:** http://localhost/register — el primer usuario que se
   registra cuando la DB esta vacia queda como admin (esto ya lo hicimos
   en el bootstrap). Los siguientes son usuarios normales.

2. **Desde el panel admin:** http://localhost/users — un admin puede:
   - Promover usuario normal → admin
   - Demover admin → usuario normal (siempre que no sea el ultimo admin)
   - Borrar usuarios (siempre que no se borre a si mismo)

---

## 9. Actualizar a la ultima version

```bash
cd monitor_doker
git pull
docker compose up -d --build
```

Esto descarga las nuevas imagenes y rebuilda los servicios que tengan
cambios. Los datos en los volumenes se preservan.

---

## 10. Troubleshooting comun

**El dashboard dice "DB no responde"**
- `docker compose logs db-central` — revisa si arranco
- `docker compose ps` — debe estar `(healthy)`
- A veces tarda 30-60s en el primer boot

**No puedo abrir Passbolt en https://localhost:8443/**
- El cert es self-signed → el navegador alerta. Click "Avanzado" → "Continuar"
- Si insiste, regenera certs: `rm -rf nginx/certs && bash scripts/setup.sh`
- Reinicia nginx: `docker compose restart nginx`

**Checkmk muestra `unknown` en el dashboard**
- Te falto el paso 6.2: crear el usuario `automation` y poner el secret en `.env`

**El puerto 80 / 443 / 8443 / 5050 ya esta en uso**
- En Windows: revisa que IIS no este corriendo
- En Linux: `sudo ss -lntp | grep ':80 '` para ver quien lo tiene
- Cambia el puerto del lado izquierdo en `docker-compose.yml`:
  `"8080:80"` en vez de `"80:80"`

**`scripts/setup.sh` falla con `bad interpreter`**
- Tu git checkeo el archivo con CRLF de Windows
- Soluciona: `git checkout-index --force -- scripts/setup.sh`
  (el `.gitattributes` ya fuerza LF, pero si lo clonaste antes no agarra)

**Quiero empezar de cero**
```bash
docker compose down -v   # borra volumenes!
rm .env nginx/certs/*    # borra config local
bash scripts/setup.sh    # regenera
docker compose up -d
```

---

## 11. Donde leer mas

- 📐 [`ARQUITECTURA.md`](ARQUITECTURA.md) — diagrama completo, donde guarda datos cada componente, por que Checkmk no usa MySQL
- 🚀 [`DEPLOY.md`](DEPLOY.md) — pasos especificos para desplegar a un server real
- 📋 [`README.md`](README.md) — tabla de versiones de cada herramienta usada
