#!/usr/bin/env bash
set -euo pipefail

# Deploy produzione Mail Exchange (Hub-only, senza sorgenti sul server).
#
# Copia sul server solo docker-compose.yml e .env; i container usano immagini
# pre-buildate su Docker Hub (cybercecco/mail-exchange-*). Nessun tar del repo,
# nessun docker compose build.
#
# Build/push immagini (macchina di sviluppo, prima del deploy):
#   docker login
#   ./scripts/build-and-push.sh
#
# Uso: ./deploy.sh [root@host] [/opt/mail-exchange] [env-file]
#   DEPLOY_ENV_FILE=.env.production.secondary ./deploy.sh root@192.168.1.69

REMOTE_HOST="${1:-root@172.22.11.125}"
REMOTE_DIR="${2:-/opt/mail-exchange}"
ENV_FILE="${DEPLOY_ENV_FILE:-${3:-.env.production}}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing env file: ${ENV_FILE}"
  exit 1
fi

echo "Deploy → ${REMOTE_HOST}:${REMOTE_DIR} (env: ${ENV_FILE})"

# Rimuovi sorgenti e artefatti build; preserva .env e override DNS già generato
ssh "${REMOTE_HOST}" bash -s -- "${REMOTE_DIR}" <<'REMOTE'
set -euo pipefail
DIR="$1"
mkdir -p "$DIR"
find "$DIR" -mindepth 1 -maxdepth 1 \
  ! -name '.env' \
  ! -name 'docker-dns.override.yml' \
  -exec rm -rf {} +
REMOTE

scp docker-compose.yml "${REMOTE_HOST}:${REMOTE_DIR}/docker-compose.yml"
scp "${ENV_FILE}" "${REMOTE_HOST}:${REMOTE_DIR}/.env"

ssh "${REMOTE_HOST}" bash -s -- "${REMOTE_DIR}" <<'REMOTE'
set -euo pipefail
DIR="$1"
cd "$DIR"

COMPOSE_ARGS=(-f docker-compose.yml)
if [[ -f docker-dns.override.yml ]]; then
  COMPOSE_ARGS+=(-f docker-dns.override.yml)
fi

docker compose "${COMPOSE_ARGS[@]}" pull
docker compose "${COMPOSE_ARGS[@]}" up -d

# Applica DNS Docker da impostazioni salvate (volume mail-data)
sleep 4
if docker cp mx-api:/data/generated/docker-dns.override.yml docker-dns.override.yml 2>/dev/null; then
  docker compose -f docker-compose.yml -f docker-dns.override.yml up -d
  echo "DNS container aggiornati da impostazioni piattaforma"
fi
REMOTE
