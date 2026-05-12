#!/usr/bin/env bash
#
# Loop principal. Espera a las dos DBs, hace sync inicial y luego repite
# cada SYNC_INTERVAL_HOURS horas. Llama a sync-now.sh para cada ejecucion
# (asi el mismo script se puede invocar manualmente).
#
set -uo pipefail

: "${SYNC_INTERVAL_HOURS:=1}"

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

log "db-sync arrancando. Intervalo=${SYNC_INTERVAL_HOURS}h, dbs=${DATABASES}"

wait_for_host "$SOURCE_HOST" "$SOURCE_USER" "$SOURCE_PASSWORD" || exit 1
wait_for_host "$TARGET_HOST" "$TARGET_USER" "$TARGET_PASSWORD" || exit 1

# Sync inicial al arrancar.
/usr/local/bin/sync-now.sh || true

# Loop con intervalo configurable. Tambien se puede correr sync-now.sh a
# mano via 'docker compose exec db-sync /usr/local/bin/sync-now.sh' sin
# parar este loop.
while true; do
    sleep "$((SYNC_INTERVAL_HOURS * 3600))"
    /usr/local/bin/sync-now.sh || true
done
