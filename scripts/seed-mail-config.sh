#!/usr/bin/env bash
# Popola config/ dai template infra/ se i file statici non esistono ancora.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_ROOT="${1:-${ROOT}/config}"
TEMPLATE_ROOT="${2:-${ROOT}/infra}"

mkdir -p "${CONFIG_ROOT}/postfix/generated" \
  "${CONFIG_ROOT}/postfix/sasl" \
  "${CONFIG_ROOT}/amavis/conf.d" \
  "${CONFIG_ROOT}/spamassassin" \
  "${CONFIG_ROOT}/clamav"

copy_if_missing() {
  local src="$1" dest="$2"
  if [[ ! -f "${dest}" ]]; then
    mkdir -p "$(dirname "${dest}")"
    cp "${src}" "${dest}"
    echo "seed: ${dest}"
  fi
}

# Repo-managed static files: always refresh from infra on deploy.
copy_static() {
  local src="$1" dest="$2"
  mkdir -p "$(dirname "${dest}")"
  cp "${src}" "${dest}"
  echo "sync: ${dest}"
}

copy_static "${TEMPLATE_ROOT}/postfix/main.cf" "${CONFIG_ROOT}/postfix/main.cf"
copy_static "${TEMPLATE_ROOT}/postfix/sasl/smtpd.conf" "${CONFIG_ROOT}/postfix/sasl/smtpd.conf"
copy_static "${TEMPLATE_ROOT}/amavis/amavisd.conf" "${CONFIG_ROOT}/amavis/conf.d/50-user"
copy_static \
  "${TEMPLATE_ROOT}/amavis/52-clamav-scanner" \
  "${CONFIG_ROOT}/amavis/conf.d/52-clamav-scanner"

touch "${CONFIG_ROOT}/postfix/generated/.gitkeep" \
  "${CONFIG_ROOT}/spamassassin/.gitkeep" \
  "${CONFIG_ROOT}/clamav/.gitkeep"

echo "Mail config ready under ${CONFIG_ROOT}"
