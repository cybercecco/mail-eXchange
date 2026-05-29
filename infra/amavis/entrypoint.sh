#!/usr/bin/env bash
set -euo pipefail

MAIL_CFG="${MAIL_CONFIG_DIR:-/mail-exchange-config}"
LOCAL_DOMAINS_FILE="${MAIL_CFG}/postfix/generated/virtual_alias_domains"
AMAVIS_LOCAL_DOMAINS="${MAIL_CFG}/amavis/local-domains.conf"

mkdir -p /etc/spamassassin
mkdir -p "${MAIL_CFG}/amavis" "${MAIL_CFG}/spamassassin" "${MAIL_CFG}/postfix/generated"
mkdir -p /data/logs
mkdir -p /data/quarantine/incoming/spam
mkdir -p /data/quarantine/incoming/virus
mkdir -p /data/quarantine/incoming/bad-header
# Amavis (uid amavis) must create letter subdirs under incoming/ (quarantine_subdir_levels).
chown -R amavis:amavis /data/quarantine/incoming 2>/dev/null || true
chmod 775 /data/quarantine/incoming 2>/dev/null || true
touch /data/logs/amavis.log
chown amavis:amavis /data/logs/amavis.log 2>/dev/null || true
chmod 664 /data/logs/amavis.log 2>/dev/null || true

if [[ ! -f /etc/mailname ]]; then
  echo "${MAIL_DOMAIN:-localhost.localdomain}" > /etc/mailname
fi

ensure_amavis_config() {
  mkdir -p /etc/amavis/conf.d "${MAIL_CFG}/amavis/conf.d"
  if [[ -f "${MAIL_CFG}/amavis/conf.d/50-user" ]]; then
    cp -f "${MAIL_CFG}/amavis/conf.d/50-user" /etc/amavis/conf.d/50-user
  elif [[ ! -f /etc/amavis/conf.d/50-user && -f /usr/share/mail-exchange/amavis/50-user ]]; then
    cp /usr/share/mail-exchange/amavis/50-user /etc/amavis/conf.d/50-user
  fi
  if [[ -f "${MAIL_CFG}/amavis/conf.d/52-clamav-scanner" ]]; then
    cp -f "${MAIL_CFG}/amavis/conf.d/52-clamav-scanner" /etc/amavis/conf.d/52-clamav-scanner
  elif [[ ! -f /etc/amavis/conf.d/52-clamav-scanner && -f /usr/share/mail-exchange/amavis/52-clamav-scanner ]]; then
    cp /usr/share/mail-exchange/amavis/52-clamav-scanner /etc/amavis/conf.d/52-clamav-scanner
  fi
}

ensure_amavis_config

touch "${MAIL_CFG}/spamassassin/local.cf"
touch "${MAIL_CFG}/amavis/spam-overrides.conf"
ln -sf "${MAIL_CFG}/spamassassin/local.cf" /etc/spamassassin/local.cf
ln -sf "${MAIL_CFG}/amavis/spam-overrides.conf" /etc/amavis/spam-overrides.conf
ln -sf "${AMAVIS_LOCAL_DOMAINS}" /etc/amavis/local-domains.conf

build_local_domains() {
  local domains=()
  if [[ -f "${LOCAL_DOMAINS_FILE}" ]]; then
    while read -r line; do
      [[ -z "${line}" ]] && continue
      local name="${line%% *}"
      [[ -n "${name}" ]] && domains+=("\"${name}\"")
    done < "${LOCAL_DOMAINS_FILE}"
  fi
  if [[ ${#domains[@]} -eq 0 && -n "${MAIL_DOMAIN:-}" ]]; then
    domains=("\"${MAIL_DOMAIN}\"")
  fi
  {
    echo "use strict;"
    echo "@local_domains_maps = ( ["
    if [[ ${#domains[@]} -gt 0 ]]; then
      echo "  $(IFS=,; echo "${domains[*]}"),"
    fi
    echo "] );"
  } > "${AMAVIS_LOCAL_DOMAINS}"
}

build_local_domains

wait_for_clamav() {
  local host="${CLAMAV_HOST:-clamav}"
  local port="${CLAMAV_PORT:-3310}"
  local max_wait="${CLAMAV_WAIT_SECONDS:-180}"
  local elapsed=0
  while (( elapsed < max_wait )); do
    if perl -MIO::Socket::INET -e "
      my \$s = IO::Socket::INET->new(
        PeerHost => q(${host}),
        PeerPort => ${port},
        Proto => q(tcp),
        Timeout => 5,
      ) or exit 1;
      print \$s \"PING\\n\";
      my \$r = <\$s>;
      exit((defined \$r && \$r =~ /^PONG/) ? 0 : 1);
    "; then
      echo "ClamAV ready at ${host}:${port}"
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  echo "ClamAV not ready at ${host}:${port} after ${max_wait}s" >&2
  return 1
}

wait_for_clamav

if [[ -n "${POSTFIX_HOSTNAME:-}" ]]; then
  cat > /etc/amavis/conf.d/51-hostname <<EOF
\$myhostname = '${POSTFIX_HOSTNAME}';
EOF
fi

watch_domains() {
  local old_sum=""
  while true; do
    if [[ -f "${LOCAL_DOMAINS_FILE}" ]]; then
      local current_sum
      current_sum="$(sha256sum "${LOCAL_DOMAINS_FILE}" | awk '{print $1}')"
      if [[ "${current_sum}" != "${old_sum}" ]]; then
        build_local_domains
        old_sum="${current_sum}"
      fi
    fi
    sleep 15
  done
}

watch_domains &

watch_spam_config() {
  local old_sum=""
  local initialized=0
  while true; do
    local files=(
      "${MAIL_CFG}/spamassassin/local.cf"
      "${MAIL_CFG}/amavis/spam-overrides.conf"
    )
    local current_sum=""
    current_sum="$(sha256sum "${files[@]}" 2>/dev/null | sha256sum | awk '{print $1}')"
    if [[ "${initialized}" -eq 1 && "${current_sum}" != "${old_sum}" ]]; then
      echo "Spam/Amavis config changed — reloading amavisd"
      if command -v amavisd-new >/dev/null 2>&1; then
        amavisd-new reload 2>/dev/null || true
      elif command -v amavisd >/dev/null 2>&1; then
        amavisd reload 2>/dev/null || true
      fi
    fi
    old_sum="${current_sum}"
    initialized=1
    sleep 15
  done
}

watch_spam_config &

if command -v amavisd-new >/dev/null 2>&1; then
  exec amavisd-new foreground
fi

exec amavisd foreground
