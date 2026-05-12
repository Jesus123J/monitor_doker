#!/usr/bin/env bash
#
# One-shot sync. Lo invoca el loop (sync.sh) y tambien se puede llamar
# manualmente con:
#   docker compose exec db-sync /usr/local/bin/sync-now.sh
#
# Variables esperadas (mismas que sync.sh):
#   SOURCE_HOST, SOURCE_USER, SOURCE_PASSWORD
#   TARGET_HOST, TARGET_USER, TARGET_PASSWORD
#   DATABASES         (lista separada por espacios)
#   STATUS_FILE       (donde escribir el ultimo resultado)
#
set -uo pipefail

: "${STATUS_FILE:=/var/lib/db-sync/last_sync}"
mkdir -p "$(dirname "$STATUS_FILE")"

log() { echo "[db-sync $(date -Iseconds)] $*"; }

log "Sync iniciado: ${DATABASES} desde $SOURCE_HOST hacia $TARGET_HOST"
started=$(date +%s)

if ! mariadb-dump \
        -h "$SOURCE_HOST" -u "$SOURCE_USER" -p"$SOURCE_PASSWORD" \
        --single-transaction --routines --triggers \
        --add-drop-database --databases $DATABASES \
    | mariadb -h "$TARGET_HOST" -u "$TARGET_USER" -p"$TARGET_PASSWORD"
then
    log "ERROR: el sync fallo"
    echo "FAIL $(date -Iseconds)" > "$STATUS_FILE"
    exit 1
fi

elapsed=$(($(date +%s) - started))
log "Sync OK en ${elapsed}s"
echo "OK $(date -Iseconds) elapsed=${elapsed}s dbs=${DATABASES}" > "$STATUS_FILE"
