#!/usr/bin/env bash
set -euo pipefail

GENERATED_OPENDKIM="/data/generated/opendkim"
DKIM_PUB_DIR="/data/generated/dkim"
DOMAINS_LIST="${GENERATED_OPENDKIM}/domains.list"
DEFAULT_SELECTOR="${DKIM_SELECTOR:-mail}"

mkdir -p /etc/opendkim/keys "${GENERATED_OPENDKIM}" "${DKIM_PUB_DIR}"
chown -R opendkim:opendkim /etc/opendkim/keys

cat > /etc/opendkim/TrustedHosts <<EOF
127.0.0.1
::1
postfix
EOF

ensure_domain_key() {
  local domain="$1"
  local selector="$2"
  local key_dir="/etc/opendkim/keys/${domain}"
  local key_file="${key_dir}/${selector}.private"
  local txt_file="${key_dir}/${selector}.txt"

  mkdir -p "${key_dir}"
  if [[ ! -f "${key_file}" ]]; then
    opendkim-genkey -D "${key_dir}" -d "${domain}" -s "${selector}"
    if [[ "${key_dir}/${selector}.private" != "${key_file}" && -f "${key_dir}/${selector}.private" ]]; then
      mv "${key_dir}/${selector}.private" "${key_file}"
    fi
  fi
  chown -R opendkim:opendkim "${key_dir}"
  chmod 600 "${key_file}" 2>/dev/null || true

  if [[ -f "${txt_file}" ]]; then
    local pubkey
    pubkey="$(tr -d '\n\r\t ()"' < "${txt_file}" | sed -n 's/.*p=\([A-Za-z0-9+/=]*\).*/\1/p')"
    if [[ -n "${pubkey}" ]]; then
      printf '%s' "${pubkey}" > "${DKIM_PUB_DIR}/${domain}.pub"
    fi
  fi
}

sync_opendkim_tables() {
  if [[ -f "${GENERATED_OPENDKIM}/KeyTable" ]]; then
    cp "${GENERATED_OPENDKIM}/KeyTable" /etc/opendkim/KeyTable
  fi
  if [[ -f "${GENERATED_OPENDKIM}/SigningTable" ]]; then
    cp "${GENERATED_OPENDKIM}/SigningTable" /etc/opendkim/SigningTable
  fi
}

bootstrap_from_env() {
  local domain="${MAIL_DOMAIN:-}"
  if [[ -n "${domain}" && ! -f "${DOMAINS_LIST}" ]]; then
    ensure_domain_key "${domain}" "${DEFAULT_SELECTOR}"
    cat > /etc/opendkim/KeyTable <<EOF
${DEFAULT_SELECTOR}._domainkey.${domain} ${domain}:${DEFAULT_SELECTOR}:/etc/opendkim/keys/${domain}/${DEFAULT_SELECTOR}.private
EOF
    cat > /etc/opendkim/SigningTable <<EOF
*@${domain} ${DEFAULT_SELECTOR}._domainkey.${domain}
EOF
  fi
}

process_domains() {
  if [[ -f "${DOMAINS_LIST}" ]]; then
    while IFS= read -r line || [[ -n "${line}" ]]; do
      [[ -z "${line}" ]] && continue
      local domain="${line%%:*}"
      local selector="${line#*:}"
      [[ "${selector}" == "${line}" ]] && selector="${DEFAULT_SELECTOR}"
      ensure_domain_key "${domain}" "${selector}"
    done < "${DOMAINS_LIST}"
    sync_opendkim_tables
    return
  fi
  bootstrap_from_env
}

process_domains

watch_opendkim() {
  local old_sum=""
  while true; do
    local current_sum=""
    if [[ -f "${DOMAINS_LIST}" ]]; then
      current_sum="$(sha256sum "${DOMAINS_LIST}" \
        "${GENERATED_OPENDKIM}/KeyTable" \
        "${GENERATED_OPENDKIM}/SigningTable" 2>/dev/null | sha256sum | awk '{print $1}')"
    fi
    if [[ -n "${current_sum}" && "${current_sum}" != "${old_sum}" ]]; then
      if [[ -n "${old_sum}" ]]; then
        process_domains
        if [[ -f /run/opendkim/opendkim.pid ]]; then
          kill -HUP "$(cat /run/opendkim/opendkim.pid)" 2>/dev/null || true
        fi
      fi
      old_sum="${current_sum}"
    fi
    sleep 10
  done
}

watch_opendkim &
exec opendkim -f -x /etc/opendkim.conf
