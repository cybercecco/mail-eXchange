#!/usr/bin/env bash
# Espone Mail Exchange su https://smtp.inxpire.support:443 tramite inxpire-web.
set -euo pipefail

REMOTE_HOST="${1:-root@172.22.11.125}"
INXPIRE_DIR="${2:-/opt/inxpire-app-qa}"
MX_NETWORK="${3:-mail-exchange_mxnet}"
MARKER="mail-exchange: smtp.inxpire.support"
SNIPPET_FILE="$(cd "$(dirname "$0")/.." && pwd)/infra/inxpire-smtp.caddy.snippet"

if [[ ! -f "$SNIPPET_FILE" ]]; then
  echo "Missing $SNIPPET_FILE"
  exit 1
fi

if ! ssh "${REMOTE_HOST}" "grep -qF '${MARKER}' '${INXPIRE_DIR}/CaddyFile' 2>/dev/null"; then
  echo "Aggiungo blocco smtp.inxpire.support a ${INXPIRE_DIR}/CaddyFile ..."
  ssh "${REMOTE_HOST}" "printf '\n' >> '${INXPIRE_DIR}/CaddyFile'"
  ssh "${REMOTE_HOST}" "cat >> '${INXPIRE_DIR}/CaddyFile'" < "$SNIPPET_FILE"
else
  echo "Blocco smtp.inxpire.support già presente."
fi

ssh "${REMOTE_HOST}" "docker network connect ${MX_NETWORK} inxpire-web 2>/dev/null || true"
ssh "${REMOTE_HOST}" "docker exec inxpire-web caddy reload --config /etc/caddy/Caddyfile"
echo "OK: https://smtp.inxpire.support servito su :443 via inxpire-web → mx-frontend"
