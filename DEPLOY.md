# Despliegue en un servidor

Pre-requisitos en el server: `git`, `docker`, `docker compose v2`, `openssl`.

## Pasos

```bash
git clone https://github.com/Jesus123J/monitor_doker.git
cd monitor_doker

# Genera los certs self-signed y copia .env.example -> .env
bash scripts/setup.sh

# IMPORTANTE: edita .env y cambia TODOS los passwords antes de levantar
nano .env

docker compose up -d
```

## Que sube y que NO sube al repo

Lo que **NO** se sube (esta en `.gitignore`):

- `.env`               - tus passwords reales
- `.claude/`           - sesion de Claude Code
- `nginx/certs/`       - certs SSL self-signed (los regenera `setup.sh`)
- `__pycache__/`       - cache de Python

Lo que **si** sube (todo lo necesario para reconstruir):

- Codigo del dashboard (`dashboard/`)
- Esquema y init de la DB (`db/init/`)
- Config de nginx (`nginx/nginx.conf`)
- `docker-compose.yml`
- `.env.example` (plantilla sin secrets)
- Logo y docs (`docs/`)

## Cambios obligatorios para un server publico

Edita `.env` antes de `docker compose up`:

| Variable | Local | Server publico |
|---|---|---|
| `DB_ROOT_PASSWORD` | hex generado | **regenera otro** |
| `PASSBOLT_DB_PASSWORD` | hex generado | **regenera otro** |
| `DASHBOARD_DB_PASSWORD` | hex generado | **regenera otro** |
| `CMK_PASSWORD` | hex generado | **regenera otro** |
| `DASHBOARD_SECRET_KEY` | hex generado | **regenera otro** |
| `DASHBOARD_ADMIN_PASSWORD` | hex generado | **regenera otro** |
| `PASSBOLT_DOMAIN` | `localhost:8443` | tu dominio real, ej: `passbolt.midominio.com` |

Para regenerar passwords aleatorios:

```bash
openssl rand -hex 32
```

## Cosas que aun NO hace este stack (no bloquean despliegue, pero piensalo)

- TLS valido (Let's Encrypt) en lugar de self-signed
- Backups automaticos de la DB (issue #10)
- Notificaciones por email (issue #8)
- 2FA (issue #7)
- Limites de CPU/RAM por contenedor

## Apagar todo / actualizar

```bash
docker compose down              # apaga, mantiene datos
docker compose down -v           # apaga y BORRA volumenes (cuidado!)
git pull && docker compose up -d --build   # actualizar a la ultima version
```
