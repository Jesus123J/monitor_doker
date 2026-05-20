#!/usr/bin/env bash
#
# Setup post-clone. Genera lo que esta en .gitignore pero hace falta para arrancar:
# - nginx/certs/passbolt.crt y .key (self-signed)
#
# Despues de correr esto:
#   1) cp .env.example .env  (y editar passwords si no copiaste el tuyo)
#   2) docker compose up -d
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CERT_DIR="$ROOT/nginx/certs"

echo "==> Verificando certificados nginx..."
mkdir -p "$CERT_DIR"

if [[ -f "$CERT_DIR/passbolt.crt" && -f "$CERT_DIR/passbolt.key" ]]; then
    echo "    Certs ya existen, no toco nada."
else
    DOMAIN="${PASSBOLT_DOMAIN:-passbolt.local}"
    DOMAIN_NO_PORT="${DOMAIN%:*}"
    echo "    Generando self-signed para CN=$DOMAIN_NO_PORT (validez 365 dias)..."

    # Config minimo inline para evitar depender del openssl.cnf del sistema
    OSSL_CFG="$(mktemp)"
    cat > "$OSSL_CFG" <<CFG
[req]
distinguished_name = dn
prompt = no
[dn]
CN = $DOMAIN_NO_PORT
CFG

    openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
        -config "$OSSL_CFG" \
        -keyout "$CERT_DIR/passbolt.key" \
        -out    "$CERT_DIR/passbolt.crt"

    rm -f "$OSSL_CFG"
    echo "    OK  -> $CERT_DIR/passbolt.crt"
fi

echo "==> Verificando archivo .env..."
if [[ -f "$ROOT/.env" ]]; then
    echo "    .env ya existe, no toco nada."
else
    if [[ ! -f "$ROOT/.env.example" ]]; then
        echo "    ATENCION: no hay .env.example."
        exit 1
    fi

    echo "    Generando .env con passwords aleatorios..."

    # Genero un set de passwords aleatorios y reemplazo los placeholders
    # del .env.example. Usuario admin del dashboard se queda con el del ejemplo
    # asi se puede entrar la primera vez sin tener que buscarlo.
    cp "$ROOT/.env.example" "$ROOT/.env"

    declare -A REPL=(
        [DB_ROOT_PASSWORD]="$(openssl rand -hex 16)"
        [PASSBOLT_DB_PASSWORD]="$(openssl rand -hex 16)"
        [DASHBOARD_DB_PASSWORD]="$(openssl rand -hex 16)"
        [UNIFIED_READER_PASSWORD]="$(openssl rand -hex 16)"
        [CMK_PASSWORD]="$(openssl rand -hex 12)"
        [DASHBOARD_SECRET_KEY]="$(openssl rand -hex 32)"
        [DASHBOARD_ADMIN_PASSWORD]="$(openssl rand -hex 8)"
    )

    for key in "${!REPL[@]}"; do
        # sed sin -i para portabilidad (mac vs gnu)
        tmp="$(mktemp)"
        awk -v k="$key" -v v="${REPL[$key]}" '
            BEGIN { FS=OFS="=" }
            $1 == k { print k "=" v; next }
            { print }
        ' "$ROOT/.env" > "$tmp"
        mv "$tmp" "$ROOT/.env"
    done

    echo "    .env generado con passwords nuevos."
    echo "    Password del admin del dashboard:"
    grep '^DASHBOARD_ADMIN_PASSWORD=' "$ROOT/.env"
fi

echo ""
echo "Setup OK. Siguiente paso:"
echo "  docker compose up -d"
echo ""
echo "Login del dashboard cuando arranque:"
grep '^DASHBOARD_ADMIN_USER='     "$ROOT/.env" 2>/dev/null || true
grep '^DASHBOARD_ADMIN_PASSWORD=' "$ROOT/.env" 2>/dev/null || true
