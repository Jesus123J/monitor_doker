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
if [[ ! -f "$ROOT/.env" ]]; then
    if [[ -f "$ROOT/.env.example" ]]; then
        cp "$ROOT/.env.example" "$ROOT/.env"
        echo "    Copie .env.example -> .env. EDITA los passwords antes de arrancar!"
    else
        echo "    ATENCION: no hay .env ni .env.example. Crealo manualmente."
    fi
else
    echo "    .env ya existe."
fi

echo ""
echo "Setup OK. Siguiente paso:"
echo "  docker compose up -d"
