#!/usr/bin/env bash
#
# Smoke test: prueba rapidamente que los 7 servicios responden.
# Uso: bash scripts/smoke-test.sh
#
set -uo pipefail

# Toma passwords del .env
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT/.env" ]]; then
    set -a; source "$ROOT/.env"; set +a
fi

PASS=0; FAIL=0
check() {
    local label="$1" code="$2"
    if [[ "$code" =~ ^(2|3) ]]; then
        echo "  ✅ $label  (HTTP $code)"
        PASS=$((PASS+1))
    else
        echo "  ❌ $label  (HTTP $code)"
        FAIL=$((FAIL+1))
    fi
}
check_cmd() {
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then
        echo "  ✅ $label"
        PASS=$((PASS+1))
    else
        echo "  ❌ $label"
        FAIL=$((FAIL+1))
    fi
}

echo "==> 1. Contenedores corriendo"
n_up=$(docker compose ps --format '{{.Status}}' | grep -c "^Up")
if [[ "$n_up" -ge 7 ]]; then
    echo "  ✅ $n_up contenedores Up"
    PASS=$((PASS+1))
else
    echo "  ❌ Solo $n_up contenedores Up (esperaba 7)"
    FAIL=$((FAIL+1))
fi

echo "==> 2. Endpoints HTTP"
check "Dashboard /healthz"    "$(curl -s -o /dev/null -w '%{http_code}' http://localhost/healthz)"
check "Passbolt /healthcheck" "$(curl -sk -o /dev/null -w '%{http_code}' https://localhost:8443/healthcheck/status.json)"
check "Checkmk login"         "$(curl -s  -o /dev/null -w '%{http_code}' http://localhost:5050/monitor/check_mk/login.py)"

echo "==> 3. DBs ping"
check_cmd "db-central ping" docker exec db-central mariadb-admin ping -uroot -p"$DB_ROOT_PASSWORD" --silent
check_cmd "db-mirror ping"  docker exec db-mirror  mariadb-admin ping -uroot -p"$DB_ROOT_PASSWORD" --silent

echo "==> 4. Schemas en db-central"
schemas=$(docker exec db-central mariadb -uroot -p"$DB_ROOT_PASSWORD" -N \
          -e "SELECT GROUP_CONCAT(SCHEMA_NAME) FROM information_schema.SCHEMATA
              WHERE SCHEMA_NAME IN ('passbolt','dashboard','checkmk','mkmonitor');" 2>/dev/null)
expected="checkmk,dashboard,mkmonitor,passbolt"
if [[ "$schemas" == "$expected" ]]; then
    echo "  ✅ Las 4 bases existen ($schemas)"
    PASS=$((PASS+1))
else
    echo "  ❌ Bases encontradas: $schemas (esperaba $expected)"
    FAIL=$((FAIL+1))
fi

echo "==> 5. Workers internos"
# Hay datos en lifecycle? (significa que el worker corrio)
n_lifecycle=$(docker exec db-central mariadb -uroot -p"$DB_ROOT_PASSWORD" -N \
              -e "SELECT COUNT(*) FROM dashboard.container_lifecycle;" 2>/dev/null || echo 0)
if [[ "$n_lifecycle" -gt 0 ]]; then
    echo "  ✅ lifecycle tracker activo ($n_lifecycle eventos)"
    PASS=$((PASS+1))
else
    echo "  ❌ lifecycle tracker sin datos"
    FAIL=$((FAIL+1))
fi

# Hay assets en mkmonitor?
n_assets=$(docker exec db-central mariadb -uroot -p"$DB_ROOT_PASSWORD" -N \
           -e "SELECT COUNT(*) FROM mkmonitor.assets;" 2>/dev/null || echo 0)
if [[ "$n_assets" -ge 7 ]]; then
    echo "  ✅ mkmonitor tiene $n_assets assets"
    PASS=$((PASS+1))
else
    echo "  ❌ mkmonitor tiene $n_assets assets (esperaba >=7)"
    FAIL=$((FAIL+1))
fi

echo "==> 6. Read-only user funciona"
if docker exec db-central mariadb -uunified_reader -p"$UNIFIED_READER_PASSWORD" \
        -e "SELECT 1 FROM passbolt.users LIMIT 1;" >/dev/null 2>&1; then
    echo "  ✅ unified_reader puede leer"
    PASS=$((PASS+1))
else
    echo "  ❌ unified_reader no puede leer"
    FAIL=$((FAIL+1))
fi
if docker exec db-central mariadb -uunified_reader -p"$UNIFIED_READER_PASSWORD" \
        -e "INSERT INTO passbolt.users (id) VALUES ('test');" >/dev/null 2>&1; then
    echo "  ❌ unified_reader puede ESCRIBIR (no deberia)"
    FAIL=$((FAIL+1))
else
    echo "  ✅ unified_reader NO puede escribir (correcto)"
    PASS=$((PASS+1))
fi

echo ""
echo "============================================"
echo "  Resultado: $PASS OK / $FAIL FALLOS"
echo "============================================"
[[ "$FAIL" == "0" ]] && exit 0 || exit 1
