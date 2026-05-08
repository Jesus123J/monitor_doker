#!/bin/bash
set -euo pipefail

# Este script lo ejecuta MariaDB en su primer arranque.
# Las env vars vienen del docker-compose.yml.

mariadb --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE DATABASE IF NOT EXISTS dashboard
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS '${DASHBOARD_DB_USER}'@'%'
    IDENTIFIED BY '${DASHBOARD_DB_PASSWORD}';
GRANT ALL PRIVILEGES ON dashboard.* TO '${DASHBOARD_DB_USER}'@'%';

GRANT ALL PRIVILEGES ON passbolt.* TO '${MYSQL_USER}'@'%';

FLUSH PRIVILEGES;
SQL

# Las tablas del dashboard las crea Flask (db.create_all()) en el bootstrap.
echo "DB central inicializada: bases passbolt + dashboard listas."
