#!/usr/bin/env bash
# Build e push delle immagini custom Mail Exchange su Docker Hub.
#
# Namespace: DOCKERHUB_NAMESPACE (default: DOCKERHUB_USERNAME o utente da docker login).
# Tag: MAIL_EXCHANGE_IMAGE_TAG (default: latest) + opzionale tag git short sha.
#
# Login manuale (non committare credenziali):
#   docker login
# oppure:
#   export DOCKERHUB_USERNAME=... && echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USERNAME" --password-stdin
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

detect_namespace() {
  if [[ -n "${DOCKERHUB_NAMESPACE:-}" ]]; then
    echo "$DOCKERHUB_NAMESPACE"
    return
  fi
  if [[ -n "${DOCKERHUB_USERNAME:-}" ]]; then
    echo "$DOCKERHUB_USERNAME"
    return
  fi
  local user
  user="$(docker system info 2>/dev/null | awk -F': ' '/^ Username:/ {print $2; exit}')"
  if [[ -n "$user" ]]; then
    echo "$user"
    return
  fi
  echo "mail-exchange" >&2
  echo "Impossibile rilevare namespace Docker Hub. Imposta DOCKERHUB_NAMESPACE." >&2
  exit 1
}

NAMESPACE="$(detect_namespace)"
TAG="${MAIL_EXCHANGE_IMAGE_TAG:-latest}"
PUSH="${PUSH:-1}"

declare -A SERVICES=(
  [api]="api/Dockerfile"
  [frontend]="frontend/Dockerfile"
  [caddy]="infra/caddy/Dockerfile"
  [postfix]="infra/postfix/Dockerfile"
  [amavis]="infra/amavis/Dockerfile"
  [opendkim]="infra/opendkim/Dockerfile"
)

GIT_SHA=""
if command -v git >/dev/null 2>&1 && git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  GIT_SHA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || true)"
fi

echo "==> Namespace: ${NAMESPACE}"
echo "==> Tag: ${TAG}${GIT_SHA:+ (+ sha:${GIT_SHA})}"

for svc in api frontend caddy postfix amavis opendkim; do
  dockerfile="${SERVICES[$svc]}"
  image="${NAMESPACE}/mail-exchange-${svc}"
  echo ""
  echo "==> Build ${image}:${TAG} (${dockerfile})"
  docker build -f "$dockerfile" -t "${image}:${TAG}" .
  if [[ -n "$GIT_SHA" ]]; then
    docker tag "${image}:${TAG}" "${image}:${GIT_SHA}"
  fi
done

if [[ "$PUSH" != "1" ]]; then
  echo ""
  echo "Build completate (PUSH=0, skip push)."
  exit 0
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker non disponibile." >&2
  exit 1
fi

echo ""
echo "==> Push su docker.io/${NAMESPACE}/mail-exchange-*"

for svc in api frontend caddy postfix amavis opendkim; do
  image="${NAMESPACE}/mail-exchange-${svc}"
  echo "    push ${image}:${TAG}"
  docker push "${image}:${TAG}"
  if [[ -n "$GIT_SHA" ]]; then
    echo "    push ${image}:${GIT_SHA}"
    docker push "${image}:${GIT_SHA}"
  fi
done

echo ""
echo "Push completato."
echo "Deploy remoto:"
echo "  DOCKERHUB_NAMESPACE=${NAMESPACE} MAIL_EXCHANGE_IMAGE_TAG=${TAG} \\"
echo "    docker compose -f docker-compose.yml -f docker-compose.hub.yml pull"
echo "  DOCKERHUB_NAMESPACE=${NAMESPACE} MAIL_EXCHANGE_IMAGE_TAG=${TAG} \\"
echo "    docker compose -f docker-compose.yml -f docker-compose.hub.yml up -d --no-build"
