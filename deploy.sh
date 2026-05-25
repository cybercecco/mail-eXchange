#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${1:-root@172.22.11.125}"
REMOTE_DIR="${2:-/opt/mail-exchange}"

if [[ ! -f ".env.production" ]]; then
  echo "Missing .env.production in project root"
  exit 1
fi

ssh "${REMOTE_HOST}" "mkdir -p '${REMOTE_DIR}' && find '${REMOTE_DIR}' -mindepth 1 -maxdepth 1 ! -name '.env' -exec rm -rf {} +"

tar \
  --exclude ".git" \
  --exclude "__pycache__" \
  --exclude "node_modules" \
  --exclude ".env" \
  --exclude ".env.production" \
  -czf - . | ssh "${REMOTE_HOST}" "cd '${REMOTE_DIR}' && tar -xzf -"

scp ".env.production" "${REMOTE_HOST}:${REMOTE_DIR}/.env"

ssh "${REMOTE_HOST}" "cd '${REMOTE_DIR}' && docker compose pull && docker compose up -d --build"

# Applica DNS Docker da impostazioni salvate (volume mail-data)
ssh "${REMOTE_HOST}" bash -s -- "${REMOTE_DIR}" <<'REMOTE'
set -euo pipefail
DIR="$1"
sleep 4
if docker cp mx-api:/data/generated/docker-dns.override.yml "${DIR}/docker-dns.override.yml" 2>/dev/null; then
  cd "${DIR}"
  docker compose -f docker-compose.yml -f docker-dns.override.yml up -d
  echo "DNS container aggiornati da impostazioni piattaforma"
fi
REMOTE
