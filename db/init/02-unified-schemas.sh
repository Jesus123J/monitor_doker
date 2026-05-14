#!/bin/bash
#
# Crea los schemas adicionales para la unificacion:
# - checkmk    : copia local del estado de hosts (snapshots via API)
# - mkmonitor  : sistema de monitoreo propio con assets/alerts/incidents
# - unified_reader : usuario MySQL con SELECT en TODAS las bases
#
set -euo pipefail

UNIFIED_READER_PASS="${UNIFIED_READER_PASSWORD:-changeme_unified_reader}"

mariadb --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL

-- =============================
-- Schema 'checkmk'
-- =============================
CREATE DATABASE IF NOT EXISTS checkmk
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE checkmk;

CREATE TABLE IF NOT EXISTS host_snapshots (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    snapshot_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    host_name    VARCHAR(128) NOT NULL,
    state        INT,                       -- 0=OK 1=WARN 2=CRIT 3=UNKNOWN
    state_text   VARCHAR(16),
    output       TEXT,
    last_check   TIMESTAMP NULL,
    acknowledged TINYINT(1) DEFAULT 0,
    INDEX idx_host_time (host_name, snapshot_at),
    INDEX idx_time (snapshot_at)
) ENGINE=InnoDB;

-- =============================
-- Schema 'mkmonitor' (sistema de monitoreo propio)
-- =============================
CREATE DATABASE IF NOT EXISTS mkmonitor
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE mkmonitor;

CREATE TABLE IF NOT EXISTS assets (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    hostname    VARCHAR(128) NOT NULL UNIQUE,
    ip_address  VARCHAR(45),
    type        ENUM('server','router','switch','vm','container','other') DEFAULT 'server',
    criticality ENUM('low','medium','high','critical') DEFAULT 'medium',
    owner_email VARCHAR(128),
    description VARCHAR(255),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS contacts (
    id      INT AUTO_INCREMENT PRIMARY KEY,
    name    VARCHAR(128) NOT NULL,
    email   VARCHAR(128) NOT NULL,
    phone   VARCHAR(32),
    role    VARCHAR(64),
    on_call TINYINT(1) DEFAULT 0
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS incidents (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    asset_id    INT,
    title       VARCHAR(255) NOT NULL,
    description TEXT,
    severity    ENUM('info','warning','critical') DEFAULT 'warning',
    status      ENUM('open','acknowledged','closed') DEFAULT 'open',
    assigned_to INT,
    opened_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at   TIMESTAMP NULL,
    INDEX idx_asset (asset_id),
    INDEX idx_status (status),
    FOREIGN KEY (asset_id)    REFERENCES assets(id)   ON DELETE SET NULL,
    FOREIGN KEY (assigned_to) REFERENCES contacts(id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS alerts (
    id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    asset_id   INT,
    metric     VARCHAR(64) NOT NULL,
    value      DECIMAL(15,4),
    threshold  DECIMAL(15,4),
    level      ENUM('warning','critical') DEFAULT 'warning',
    fired_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP NULL,
    INDEX idx_asset_time (asset_id, fired_at),
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Datos de ejemplo para que la UI no este vacia
INSERT IGNORE INTO contacts (name, email, phone, role, on_call) VALUES
    ('Jesus Gutierrez', 'jesus@utp.local', '+51 9XX XXX XXX', 'SRE', 1),
    ('Equipo Ops',     'ops@utp.local',   NULL,              'team', 0);

INSERT IGNORE INTO assets (hostname, ip_address, type, criticality, owner_email, description) VALUES
    ('web-prod-01',    '10.0.0.10',  'server', 'critical', 'jesus@utp.local', 'Servidor web produccion'),
    ('db-prod-01',     '10.0.0.20',  'server', 'critical', 'jesus@utp.local', 'Base de datos produccion'),
    ('router-edge',    '10.0.0.1',   'router', 'high',     'ops@utp.local',   'Router de borde'),
    ('localhost',      '127.0.0.1',  'server', 'low',      'jesus@utp.local', 'Host local de pruebas');

INSERT INTO incidents (asset_id, title, description, severity, status, assigned_to) VALUES
    ((SELECT id FROM assets WHERE hostname='web-prod-01'),
     'Latencia alta en web-prod-01',
     'p95 > 2s durante 10 min',
     'warning', 'open',
     (SELECT id FROM contacts WHERE email='jesus@utp.local'));

INSERT INTO alerts (asset_id, metric, value, threshold, level) VALUES
    ((SELECT id FROM assets WHERE hostname='web-prod-01'),
     'response_time_ms', 2350, 2000, 'warning'),
    ((SELECT id FROM assets WHERE hostname='db-prod-01'),
     'cpu_pct', 95.4, 85, 'critical');

-- =============================
-- Usuario READ-ONLY para el agregador
-- =============================
CREATE USER IF NOT EXISTS 'unified_reader'@'%' IDENTIFIED BY '${UNIFIED_READER_PASS}';
GRANT SELECT ON passbolt.*  TO 'unified_reader'@'%';
GRANT SELECT ON dashboard.* TO 'unified_reader'@'%';
GRANT SELECT ON checkmk.*   TO 'unified_reader'@'%';
GRANT SELECT ON mkmonitor.* TO 'unified_reader'@'%';

FLUSH PRIVILEGES;
SQL

echo "Schemas unificados creados: passbolt, dashboard, checkmk, mkmonitor + unified_reader."
