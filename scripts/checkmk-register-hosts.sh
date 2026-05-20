#!/usr/bin/env bash
#
# Registra los 6 servicios del stack como hosts en Checkmk.
# Usa la API REST con el usuario 'automation'.
#
# Pre-requisitos:
#   - Tener Checkmk corriendo
#   - Haber creado el usuario 'automation' en la UI (Setup -> Users)
#   - Tener CMK_AUTOMATION_SECRET en .env
#
# Que hace:
#   1. Agrega cada host con su nombre de contenedor (db-central, passbolt, etc.)
#   2. Hace service discovery (Checkmk detecta el ICMP ping para cada uno)
#   3. Activa los cambios
#
# Despues vas a ver los hosts en:
#   http://TU-SERVER:5050/monitor/check_mk/index.py
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$ROOT/.env" ]] && { set -a; source "$ROOT/.env"; set +a; }

CMK_SITE="${CMK_SITE_ID:-monitor}"
SECRET="${CMK_AUTOMATION_SECRET:-}"
URL_BASE="http://localhost:5050/monitor/check_mk/api/1.0"

if [[ -z "$SECRET" || "$SECRET" == "cambia_automation_secret" ]]; then
    echo "ERROR: CMK_AUTOMATION_SECRET no esta configurado en .env"
    echo "Crea el usuario 'automation' en Checkmk:"
    echo "  http://localhost:5050/monitor/ -> Setup -> Users -> Add user"
    echo "  Username: automation, Role: Administrator"
    echo "Despues copia el 'automation secret' y pegalo en .env"
    exit 1
fi

AUTH="Authorization: Bearer automation $SECRET"

# Los 6 servicios del stack que NO son checkmk (ya esta monitoreado a si mismo)
HOSTS=(
    "db-central"
    "db-mirror"
    "db-sync"
    "passbolt"
    "dashboard"
    "nginx"
)

echo "==> Registrando hosts en Checkmk..."
for host in "${HOSTS[@]}"; do
    # Cada host puede ya existir, asi que ignoramos errores 400 por duplicado
    resp=$(curl -s -o /tmp/cmk_resp -w "%{http_code}" \
        -H "$AUTH" \
        -H "Content-Type: application/json" \
        -X POST "$URL_BASE/domain-types/host_config/collections/all" \
        -d "{
            \"host_name\": \"$host\",
            \"folder\": \"/\",
            \"attributes\": {
                \"ipaddress\": \"$host\",
                \"tag_address_family\": \"ip-v4-only\"
            }
        }")
    case "$resp" in
        200|201|204) echo "  [OK] $host agregado" ;;
        *)
            if grep -q "already exists\|already in use" /tmp/cmk_resp; then
                echo "  [SKIP] $host ya existe"
            else
                echo "  [FAIL] $host -> HTTP $resp"
                cat /tmp/cmk_resp
                echo
            fi
            ;;
    esac
done

echo ""
echo "==> Disparando service discovery (detecta ICMP/PING por host)..."
for host in "${HOSTS[@]}"; do
    resp=$(curl -s -o /tmp/cmk_resp -w "%{http_code}" \
        -H "$AUTH" \
        -H "Content-Type: application/json" \
        -X POST "$URL_BASE/domain-types/service_discovery_run/actions/start/invoke" \
        -d "{\"host_name\": \"$host\", \"mode\": \"refresh\"}")
    case "$resp" in
        200|202) echo "  [OK] discovery lanzado para $host" ;;
        *)       echo "  [WARN] $host -> HTTP $resp (puede ser normal)" ;;
    esac
done

echo ""
echo "==> Activando los cambios..."
resp=$(curl -s -o /tmp/cmk_resp -w "%{http_code}" \
    -H "$AUTH" \
    -H "Content-Type: application/json" \
    -H "If-Match: *" \
    -X POST "$URL_BASE/domain-types/activation_run/actions/activate-changes/invoke" \
    -d "{\"redirect\": false, \"sites\": [\"$CMK_SITE\"], \"force_foreign_changes\": true}")
case "$resp" in
    200|201|202) echo "  [OK] cambios activados" ;;
    422)         echo "  [SKIP] no habia cambios pendientes" ;;
    *)
        echo "  [FAIL] HTTP $resp"
        cat /tmp/cmk_resp
        ;;
esac

rm -f /tmp/cmk_resp

echo ""
echo "Listo. Esperá 1-2 minutos y entra a:"
echo "  http://TU-SERVER:5050/monitor/check_mk/index.py"
echo "Vas a ver los 7 hosts (los 6 nuevos + checkmk) en 'All hosts'."
