#!/usr/bin/env bash
#
# Sincroniza periodicamente db-central (origen) hacia db-mirror (destino).
# Hace dump completo + import. Las DBs en el mirror se reemplazan.
#
# Variables esperadas:
#   SOURCE_HOST, SOURCE_USER, SOURCE_PASSWORD
#   TARGET_HOST, TARGET_USER, TARGET_PASSWORD
#   DATABASES                (lista separada por espacios)
#   SYNC_INTERVAL_HOURS      (entero)
#   STATUS_FILE              (ruta donde se escribe el ultimo sync)
#
set -uo pipefail

: "${SYNC_INTERVAL_HOURS:=2}"
: "${STATUS_FILE:=/var/lib/db-sync/last_sync}"

mkdir -p "$(dirname "$STATUS_FILE")"

log() { echo "[db-sync $(date -Iseconds)] $*"; }

wait_for_host() {
    local host="$1" user="$2" pass="$3" tries=60
    while ((tries-- > 0)); do
        if mariadb-admin ping -h "$host" -u "$user" -p"$pass" --silent 2>/dev/null; then
            return 0
        fi
        sleep 2
    done
    log "ERROR: $host no respondio en 120s"
    return 1
}

do_sync() {
    log "Sync iniciado: ${DATABASES} desde $SOURCE_HOST hacia $TARGET_HOST"
    local started=$(date +%s)

    # --add-drop-database + --databases para que el import recree limpio.
    # --single-transaction evita lockear InnoDB.
    # --routines y --triggers para no perder logica.
    if ! mariadb-dump \
            -h "$SOURCE_HOST" -u "$SOURCE_USER" -p"$SOURCE_PASSWORD" \
            --single-transaction --routines --triggers \
            --add-drop-database --databases $DATABASES \
        | mariadb -h "$TARGET_HOST" -u "$TARGET_USER" -p"$TARGET_PASSWORD"
    then
        log "ERROR: el sync fallo"
        echo "FAIL $(date -Iseconds)" > "$STATUS_FILE"
        return 1
    fi

    local elapsed=$(($(date +%s) - started))
    log "Sync OK en ${elapsed}s"
    echo "OK $(date -Iseconds) elapsed=${elapsed}s dbs=${DATABASES}" > "$STATUS_FILE"
}

# --- Main ---
log "db-sync arrancando. Intervalo=${SYNC_INTERVAL_HOURS}h, dbs=${DATABASES}"

wait_for_host "$SOURCE_HOST" "$SOURCE_USER" "$SOURCE_PASSWORD" || exit 1
wait_for_host "$TARGET_HOST" "$TARGET_USER" "$TARGET_PASSWORD" || exit 1

# Sync inicial al arrancar
do_sync || true

# Loop con intervalo configurable
while true; do
    sleep "$((SYNC_INTERVAL_HOURS * 3600))"
    do_sync || true
done
