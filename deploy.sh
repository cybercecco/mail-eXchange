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

# Rimuovi sorgenti e artefatti build; preserva .env, config runtime e override DNS
ssh "${REMOTE_HOST}" bash -s -- "${REMOTE_DIR}" <<'REMOTE'
set -euo pipefail
DIR="$1"
mkdir -p "$DIR"
find "$DIR" -mindepth 1 -maxdepth 1 \
  ! -name '.env' \
  ! -name 'config' \
  ! -name 'docker-dns.override.yml' \
  -exec rm -rf {} +
REMOTE

DEPLOY_TEMPLATES="$(mktemp -d)"
trap 'rm -rf "${DEPLOY_TEMPLATES}"' EXIT
mkdir -p "${DEPLOY_TEMPLATES}/postfix/sasl" "${DEPLOY_TEMPLATES}/amavis"
cp infra/postfix/main.cf "${DEPLOY_TEMPLATES}/postfix/"
cp infra/postfix/sasl/smtpd.conf "${DEPLOY_TEMPLATES}/postfix/sasl/"
cp infra/amavis/amavisd.conf "${DEPLOY_TEMPLATES}/amavis/"
cp infra/amavis/52-clamav-scanner "${DEPLOY_TEMPLATES}/amavis/"

scp docker-compose.yml "${REMOTE_HOST}:${REMOTE_DIR}/docker-compose.yml"
scp "${ENV_FILE}" "${REMOTE_HOST}:${REMOTE_DIR}/.env"
ssh "${REMOTE_HOST}" "mkdir -p ${REMOTE_DIR}/scripts"
scp scripts/seed-mail-config.sh "${REMOTE_HOST}:${REMOTE_DIR}/scripts/seed-mail-config.sh"
scp -r "${DEPLOY_TEMPLATES}" "${REMOTE_HOST}:${REMOTE_DIR}/mail-config-templates"
ssh "${REMOTE_HOST}" bash -s -- "${REMOTE_DIR}" <<'REMOTE'
set -euo pipefail
DIR="$1"
cd "$DIR"
chmod +x scripts/seed-mail-config.sh
./scripts/seed-mail-config.sh "${DIR}/config" "${DIR}/mail-config-templates"
rm -rf "${DIR}/mail-config-templates"
REMOTE

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

# main.cf is bind-mounted via config volume; restart picks up seed-mail-config sync.
docker restart mx-postfix 2>/dev/null || true

# Applica DNS Docker da impostazioni salvate (volume mail-data)
sleep 4
if docker cp mx-api:/data/generated/docker-dns.override.yml docker-dns.override.yml 2>/dev/null; then
  docker compose -f docker-compose.yml -f docker-dns.override.yml up -d
  echo "DNS container aggiornati da impostazioni piattaforma"
fi
REMOTE
