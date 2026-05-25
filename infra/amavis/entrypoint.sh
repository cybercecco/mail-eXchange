#!/usr/bin/env bash
set -euo pipefail

mkdir -p /etc/spamassassin
mkdir -p /data/generated
mkdir -p /data/logs
touch /data/logs/amavis.log
chown amavis:amavis /data/logs/amavis.log 2>/dev/null || true
chmod 664 /data/logs/amavis.log 2>/dev/null || true

if [[ ! -f /etc/mailname ]]; then
  echo "${MAIL_DOMAIN:-localhost.localdomain}" > /etc/mailname
fi

touch /data/generated/spamassassin.local.cf
touch /data/generated/amavis-spam-overrides.conf
ln -sf /data/generated/spamassassin.local.cf /etc/spamassassin/local.cf
ln -sf /data/generated/amavis-spam-overrides.conf /etc/amavis/spam-overrides.conf

LOCAL_DOMAINS_FILE="/data/generated/virtual_alias_domains"
AMAVIS_DOMAINS="/etc/amavis/local-domains.conf"

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
  } > "${AMAVIS_DOMAINS}"
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

CLAMAV_HOST="${CLAMAV_HOST:-clamav}"
CLAMAV_PORT="${CLAMAV_PORT:-3310}"
cat > /etc/amavis/conf.d/52-clamav-scanner <<EOF
# Remote ClamAV (mx-clamav): INSTREAM over TCP, not local clamd.ctl CONTSCAN.
@av_scanners = (
  ['ClamAV-clamd',
    \\&ask_daemon, ["*", "clamd:[${CLAMAV_HOST}]:${CLAMAV_PORT}"],
    qr/\\bOK\$/m, qr/\\bFOUND\$/m,
    qr/^.*?: (?!Infected Archive)(.*) FOUND\$/m ],
);
@av_scanners_backup = ();
EOF

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

if command -v amavisd-new >/dev/null 2>&1; then
  exec amavisd-new foreground
fi

exec amavisd foreground
