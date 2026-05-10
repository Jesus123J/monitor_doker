<div align="center">
  <img src="docs/utp-logo.png" alt="Universidad Tecnologica del Peru" width="220">

  # Monitor Docker
  ### Stack contenerizado de Passbolt + Checkmk + Dashboard centralizado

  *Proyecto academico — Universidad Tecnologica del Peru*
</div>

---

## Documentacion

- 🚀 [`INICIO.md`](INICIO.md) — **Empieza aqui**: que necesitas, como arrancar, como apagar, quien puede entrar
- 📐 [`ARQUITECTURA.md`](ARQUITECTURA.md) — Diseno interno: como conecta todo, donde guarda datos cada componente
- 🌐 [`DEPLOY.md`](DEPLOY.md) — Despliegue en un server real

---

## Herramientas y versiones

| Categoria | Herramienta | Version |
|---|---|---|
| Orquestacion | Docker Engine | 25.0.3 |
| Orquestacion | Docker Compose | v2.24 |
| Base de datos | MariaDB | 11 |
| Gestor de contrasenas | Passbolt CE | latest-ce |
| Monitoreo | Checkmk Raw Edition | 2.4 |
| Reverse proxy | Nginx | 1.27 (alpine) |
| Lenguaje (dashboard) | Python | 3.12 |
| Framework web | Flask | 3.0 |
| ORM | SQLAlchemy + Flask-SQLAlchemy | 2.0 / 3.1 |
| Auth | Flask-Login + Werkzeug | 0.6 / 3.0 |
| Driver MySQL | PyMySQL | 1.1 |
| SDK Docker | docker (Python) | 7.1 |
| HTTP server | Gunicorn | 23.0 |
| HTTP client | Requests | 2.32 |
| UI | Bootstrap + Bootstrap Icons | 5.3 / 1.11 |

---

## Arquitectura

```
                        ┌─────────────┐
                        │   nginx     │  reverse proxy (80/443)
                        └──────┬──────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         │                     │                     │
    ┌────▼─────┐          ┌────▼─────┐          ┌────▼─────┐
    │ Passbolt │          │ Dashboard│          │ Checkmk  │
    │   (CE)   │          │ (Flask)  │          │   (raw)  │
    └────┬─────┘          └────┬─────┘          └──────────┘
         │                     │                     ▲
         │                     │  docker.sock        │ Web API
         │                     │  (read/write)       │
         │                     │                     │
    ┌────▼─────────────────────▼─────┐               │
    │      db-central (MariaDB)      │               │
    │  ┌─────────┐  ┌──────────────┐ │◄──────────────┘
    │  │passbolt │  │  dashboard   │ │
    │  └─────────┘  └──────────────┘ │
    └────────────────────────────────┘
```

### Componentes

- **nginx** — Reverse proxy en los puertos 80/443.
- **db-central** — Una sola instancia de MariaDB con dos bases:
  - `passbolt` — usada por Passbolt para almacenar contrasenas cifradas.
  - `dashboard` — usuarios del panel + bitacora de monitoreo y auditoria.
- **passbolt** — Gestor de contrasenas con cifrado E2E via PGP, conectado a la DB central.
- **checkmk** — Monitoreo de servidores. Mantiene su propio storage interno (RRDtool) por diseno; el dashboard lo consulta via Web API.
- **dashboard** — Aplicacion web custom con:
  - Registro y login de usuarios (passwords hasheados con scrypt).
  - Vista global del estado de Passbolt, Checkmk y la DB central.
  - Listado de contenedores (CPU, memoria, red, health, restart count).
  - Detalle por contenedor con logs en vivo, mounts, env vars (con secrets ocultos).
  - Modo experto (admin): start / stop / restart de contenedores con bitacora de auditoria.
  - Pagina de problemas (contenedores unhealthy, en restart loop, exited con error).
  - Visor del esquema de la DB del dashboard.

### Esquema de la DB del dashboard

| Tabla | Proposito |
|---|---|
| `users` | Cuentas de acceso al dashboard |
| `monitored_targets` | Catalogo de objetivos a monitorear |
| `status_log` | Historico de chequeos de estado |
| `audit_log` | Bitacora de acciones operativas (start/stop/restart) |

### Redes Docker

- **backend** — db-central, passbolt, checkmk, dashboard (no expuesta).
- **frontend** — nginx, passbolt, checkmk, dashboard (puertos publicados).
