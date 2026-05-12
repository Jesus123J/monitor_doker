<div align="center">
  <img src="docs/utp-logo.png" alt="Universidad Tecnologica del Peru" width="220">

  # Monitor Docker
  ### Stack contenerizado de Passbolt + Checkmk + Dashboard centralizado

  *Proyecto academico — Universidad Tecnologica del Peru*
</div>

---

## Que es esto

Stack en Docker Compose con:

- 🔐 **Passbolt** — gestor de contrasenas cifrado E2E
- 🖥️ **Checkmk** — monitoreo de servidores
- 🏠 **Dashboard custom (Flask)** — un solo panel que ve todo: contenedores, servicios, logs, problemas, espejo de la DB
- 💾 **DB central + DB espejo** — MariaDB centralizada con replica cada 2h
- 🚪 **Nginx** — reverse proxy y TLS unico

## Documentacion

| Doc | Cuando leerla |
|---|---|
| 🚀 [`INICIO.md`](INICIO.md) | Recien clonaste, queres arrancar |
| 📐 [`ARQUITECTURA.md`](ARQUITECTURA.md) | Queres entender como funciona por dentro |
| 💾 [`DATOS_GUARDADOS.md`](DATOS_GUARDADOS.md) | Donde guarda cada app sus datos (tablas, logs, auditoria) |
| 🌐 [`DEPLOY.md`](DEPLOY.md) | Queres subirlo a un server real |

## Stack tecnico

| Categoria | Herramienta | Version |
|---|---|---|
| Orquestacion | Docker Engine + Compose v2 | 25 / v2.24 |
| Base de datos | MariaDB | 11 |
| Gestor de contrasenas | Passbolt CE | latest-ce |
| Monitoreo | Checkmk Raw Edition | 2.4 |
| Reverse proxy | Nginx | 1.27 alpine |
| Backend dashboard | Python + Flask + SQLAlchemy | 3.12 / 3.0 / 2.0 |
| Frontend dashboard | Bootstrap | 5.3 |

## Inicio rapido

```bash
git clone https://github.com/Jesus123J/monitor_doker.git
cd monitor_doker
bash scripts/setup.sh        # genera certs y .env
docker compose up -d
```

Luego abre **http://localhost/** y entra. Detalles en [`INICIO.md`](INICIO.md).
