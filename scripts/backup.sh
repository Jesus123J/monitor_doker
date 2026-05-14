#!/usr/bin/env bash
#
# Backup consistente de las 4 bases (passbolt, dashboard, checkmk, mkmonitor)
# desde db-central. Usa --single-transaction para no lockear.
#
# Uso: bash scripts/backup.sh [output_dir]
# Default: ./backups/
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${1:-$ROOT/backups}"
TS=$(date +%Y%m%d_%H%M%S)

mkdir -p "$OUT_DIR"

# Tomamos el password de .env sin hardcodear
PASS=$(grep '^DB_ROOT_PASSWORD=' "$ROOT/.env" | cut -d= -f2-)
if [[ -z "$PASS" ]]; then
    echo "ERROR: no encuentro DB_ROOT_PASSWORD en .env"
    exit 1
fi

FILE="$OUT_DIR/db-central-$TS.sql.gz"

echo "Backup -> $FILE"
docker exec db-central mariadb-dump \
        -uroot -p"$PASS" \
        --single-transaction --routines --triggers \
        --add-drop-database \
        --databases passbolt dashboard checkmk mkmonitor \
    | gzip > "$FILE"

echo "OK ($(du -h "$FILE" | cut -f1))"

# Rotacion: mantiene los ultimos 14 backups
ls -1t "$OUT_DIR"/db-central-*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm --
echo "Rotacion aplicada (>14 archivos)."
