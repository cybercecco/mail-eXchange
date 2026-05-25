#!/usr/bin/env bash
set -euo pipefail

sync_maps() {
  cp /data/generated/virtual_mailbox_maps /etc/postfix/generated/virtual_mailbox_maps
  cp /data/generated/virtual_mailbox_catchall /etc/postfix/generated/virtual_mailbox_catchall
  cp /data/generated/transport_maps /etc/postfix/generated/transport_maps
  cp /data/generated/virtual_alias_domains /etc/postfix/generated/virtual_alias_domains
  cp /data/generated/virtual_alias_maps /etc/postfix/generated/virtual_alias_maps
  cp /data/generated/relay_sender_access /etc/postfix/generated/relay_sender_access
  rm -f /etc/postfix/generated/relay_client_access_*.cidr
  for f in /data/generated/relay_client_access_*.cidr; do
    [[ -f "${f}" ]] || continue
    cp "${f}" "/etc/postfix/generated/$(basename "${f}")"
  done
}

sync_sasl_users() {
  local passwd_file="/data/sasl/relay_passwd"
  mkdir -p /data/sasl
  touch "${passwd_file}"
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%%#*}"
    line="$(printf '%s' "${line}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -n "${line}" ]] || continue
    local user="${line%%:*}"
    local pass="${line#*:}"
    [[ -n "${user}" && -n "${pass}" && "${user}" != "${line}" ]] || continue
    saslpasswd2 -c -p "${pass}" "${user}" 2>/dev/null || true
  done < "${passwd_file}"
}

caddy_cert_paths() {
  local hostname="$1"
  local base="${CADDY_CERTS_DIR:-/caddy-data/caddy/certificates/acme-v02.api.letsencrypt.org-directory}"
  printf '%s\n' "${base}/${hostname}/${hostname}.crt" "${base}/${hostname}/${hostname}.key"
}

install_smtpd_tls_certs() {
  local hostname="${MYHOSTNAME:-}"
  local dest_dir="/etc/postfix/tls"
  local dest_cert="${dest_dir}/smtpd.crt"
  local dest_key="${dest_dir}/smtpd.key"
  mkdir -p "${dest_dir}"

  local src_cert="" src_key=""
  if [[ -n "${hostname}" ]]; then
    local paths=()
    mapfile -t paths < <(caddy_cert_paths "${hostname}")
    src_cert="${paths[0]:-}"
    src_key="${paths[1]:-}"
    if [[ ! -f "${src_cert}" || ! -f "${src_key}" ]]; then
      src_cert=""
      src_key=""
    fi
  fi

  if [[ -n "${SMTPD_TLS_CERT_FILE:-}" && -n "${SMTPD_TLS_KEY_FILE:-}" ]]; then
    src_cert="${SMTPD_TLS_CERT_FILE}"
    src_key="${SMTPD_TLS_KEY_FILE}"
  fi

  if [[ -n "${src_cert}" && -f "${src_cert}" && -f "${src_key}" ]]; then
    cp -f "${src_cert}" "${dest_cert}"
    cp -f "${src_key}" "${dest_key}"
  elif [[ ! -f "${dest_cert}" || ! -f "${dest_key}" ]]; then
    openssl req -new -x509 -days 3650 -nodes \
      -subj "/CN=${hostname:-localhost}" \
      -keyout "${dest_key}" -out "${dest_cert}" 2>/dev/null
  fi

  chmod 644 "${dest_cert}"
  chmod 640 "${dest_key}"
  chgrp postfix "${dest_key}" 2>/dev/null || chmod 644 "${dest_key}"

  postconf -e "smtpd_tls_cert_file = ${dest_cert}"
  postconf -e "smtpd_tls_key_file = ${dest_key}"
  postconf -e "smtpd_tls_security_level = may"
}

parse_queue_messages() {
  local status="$1"
  local output="$2"
  if printf '%s' "${output}" | grep -q "Mail queue is empty"; then
    printf '[]'
    return
  fi
  printf '%s\n' "${output}" | awk -v status="${status}" -f /usr/local/bin/parse_postqueue.awk
}

write_queue_snapshot() {
  local active=0 deferred=0 hold=0 total=0
  local active_out deferred_out hold_out
  local active_messages deferred_messages hold_messages
  active_out="$(postqueue -p 2>/dev/null || true)"
  if printf '%s' "${active_out}" | grep -q "Mail queue is empty"; then
    active=0
    active_messages="[]"
  else
    active="$(printf '%s\n' "${active_out}" | grep -Ec '^[0-9A-F]' || true)"
    active="${active:-0}"
    active_messages="$(parse_queue_messages active "${active_out}")"
  fi
  deferred_out="$(postqueue -p deferred 2>/dev/null || true)"
  if printf '%s' "${deferred_out}" | grep -q "Mail queue is empty"; then
    deferred=0
    deferred_messages="[]"
  else
    deferred="$(printf '%s\n' "${deferred_out}" | grep -Ec '^[0-9A-F]' || true)"
    deferred="${deferred:-0}"
    deferred_messages="$(parse_queue_messages deferred "${deferred_out}")"
  fi
  hold_out="$(postqueue -p hold 2>/dev/null || true)"
  if printf '%s' "${hold_out}" | grep -q "Mail queue is empty"; then
    hold=0
    hold_messages="[]"
  else
    hold="$(printf '%s\n' "${hold_out}" | grep -Ec '^[0-9A-F]' || true)"
    hold="${hold:-0}"
    hold_messages="$(parse_queue_messages hold "${hold_out}")"
  fi
  total=$((active + deferred + hold))
  mkdir -p /data/stats
  cat > /data/stats/queue.json.tmp <<EOF
{"total":${total},"active":${active},"deferred":${deferred},"hold":${hold},"updated_at":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","messages":{"active":${active_messages},"deferred":${deferred_messages},"hold":${hold_messages}}}
EOF
  mv /data/stats/queue.json.tmp /data/stats/queue.json
}

delete_queue_ids_from_output() {
  local output="$1"
  printf '%s\n' "${output}" | grep -E '^[0-9A-F]' | awk '{print $1}' | while read -r qid; do
    [[ -n "${qid}" ]] || continue
    postsuper -d "${qid}" 2>/dev/null || true
  done
}

process_queue_command() {
  local cmd_file="/data/stats/queue_command.json"
  [[ -f "${cmd_file}" ]] || return
  local status
  status="$(jq -r '.status // "pending"' "${cmd_file}" 2>/dev/null || echo pending)"
  [[ "${status}" == "pending" ]] || return

  local action result_msg="ok" result_ok=0
  action="$(jq -r '.action // empty' "${cmd_file}" 2>/dev/null || true)"
  case "${action}" in
    flush)
      local queue_type
      queue_type="$(jq -r '.queue_type // "deferred"' "${cmd_file}")"
      case "${queue_type}" in
        active)
          postqueue -f 2>/dev/null || true
          ;;
        hold)
          postqueue -i ALL 2>/dev/null || true
          ;;
        deferred|*)
          postqueue -f 2>/dev/null || true
          ;;
      esac
      result_msg="flush completed"
      ;;
    delete)
      local delete_all queue_type
      delete_all="$(jq -r '.delete_all // false' "${cmd_file}")"
      queue_type="$(jq -r '.queue_type // "all"' "${cmd_file}")"
      if [[ "${delete_all}" == "true" ]]; then
        case "${queue_type}" in
          deferred)
            postsuper -d ALL 2>/dev/null || true
            ;;
          active)
            delete_queue_ids_from_output "$(postqueue -p 2>/dev/null || true)"
            ;;
          hold)
            delete_queue_ids_from_output "$(postqueue -p hold 2>/dev/null || true)"
            ;;
          all|*)
            postsuper -d ALL 2>/dev/null || true
            delete_queue_ids_from_output "$(postqueue -p 2>/dev/null || true)"
            delete_queue_ids_from_output "$(postqueue -p hold 2>/dev/null || true)"
            ;;
        esac
        result_msg="delete_all completed (${queue_type})"
      else
        while read -r qid; do
          [[ -n "${qid}" ]] || continue
          postsuper -d "${qid}" 2>/dev/null || true
        done < <(jq -r '.queue_ids[]? // empty' "${cmd_file}")
        result_msg="delete completed"
      fi
      ;;
    hold_all)
      postsuper -h ALL 2>/dev/null || true
      result_msg="hold_all completed (postsuper -h ALL)"
      ;;
    release_all)
      postsuper -r ALL 2>/dev/null || true
      result_msg="release_all completed (postsuper -r ALL)"
      ;;
    postfix_pause)
      postfix pause 2>/dev/null || true
      result_msg="postfix pause completed"
      ;;
    postfix_resume)
      postfix resume 2>/dev/null || true
      result_msg="postfix resume completed"
      ;;
    *)
      result_msg="unknown action: ${action}"
      result_ok=1
      ;;
  esac

  local finished_at result_status="done"
  [[ ${result_ok} -eq 0 ]] || result_status="error"
  finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  jq -n \
    --arg action "${action}" \
    --arg status "${result_status}" \
    --arg message "${result_msg}" \
    --arg finished_at "${finished_at}" \
    '{action: $action, status: $status, message: $message, finished_at: $finished_at}' \
    > "${cmd_file}.tmp" 2>/dev/null && mv "${cmd_file}.tmp" "${cmd_file}" || rm -f "${cmd_file}.tmp"
  write_queue_snapshot
}

watch_queue() {
  while true; do
    process_queue_command
    write_queue_snapshot
    sleep 5
  done
}

mkdir -p /etc/postfix/generated
mkdir -p /data/generated
mkdir -p /data/logs /data/stats
touch /data/logs/postfix.log
watch_queue &

touch /data/generated/virtual_mailbox_maps
touch /data/generated/virtual_mailbox_catchall
touch /data/generated/transport_maps
touch /data/generated/virtual_alias_domains
touch /data/generated/virtual_alias_maps
touch /data/generated/relay_sender_access

sync_maps

# Chrooted smtp(8) needs Docker DNS to reach amavis/opendkim by service name.
mkdir -p /var/spool/postfix/etc
cp -f /etc/resolv.conf /var/spool/postfix/etc/resolv.conf

if [[ -n "${MYHOSTNAME:-}" ]]; then
  postconf -e "myhostname = ${MYHOSTNAME}"
fi
if [[ -n "${MYNETWORKS:-}" ]]; then
  postconf -e "mynetworks = ${MYNETWORKS}"
fi
install_smtpd_tls_certs
# Debian Postfix ships virtual_mailbox_base unset but postconf may expose it as empty;
# virtual(8) then fatals when transport_maps has no match.
postconf -X virtual_mailbox_base 2>/dev/null || true

mkdir -p /etc/postfix/sasl
sync_sasl_users

configure_submission() {
  if grep -qE '^submission[[:space:]]+inet' /etc/postfix/master.cf; then
    return 0
  fi
  cat >> /etc/postfix/master.cf <<'EOF'

submission inet n       -       y       -       -       smtpd
  -o syslog_name=postfix/submission
  -o smtpd_tls_security_level=encrypt
  -o smtpd_sasl_auth_enable=yes
  -o smtpd_tls_auth_only=yes
  -o smtpd_relay_restrictions=permit_mynetworks,permit_sasl_authenticated,check_sender_access hash:/etc/postfix/generated/relay_sender_access,reject
  -o smtpd_recipient_restrictions=reject_unauth_destination,reject_non_fqdn_recipient,reject_unknown_recipient_domain
EOF
}

configure_submission

if ! grep -q "smtp-amavis" /etc/postfix/master.cf; then
  cat >> /etc/postfix/master.cf <<'EOF'

smtp-amavis unix -      -       y       -       2       smtp
  -o smtp_data_done_timeout=1200
  -o smtp_send_xforward_command=yes
  -o disable_dns_lookups=yes
  -o max_use=20

10025 inet n  -       y       -       -       smtpd
  -o content_filter=
  -o local_recipient_maps=
  -o relay_recipient_maps=
  -o smtpd_restriction_classes=
  -o smtpd_delay_reject=no
  -o smtpd_client_restrictions=permit_mynetworks,reject
  -o smtpd_helo_restrictions=
  -o smtpd_sender_restrictions=
  -o smtpd_recipient_restrictions=permit_mynetworks,reject
  -o strict_rfc821_envelopes=yes
  -o receive_override_options=no_header_body_checks,no_unknown_recipient_checks
EOF
fi

# Reinjection port must be reachable from Amavis on the Docker network.
sed -i 's/^127\.0\.0\.1:10025 inet/10025 inet/' /etc/postfix/master.cf
sed -i '/^10025 inet/,/^[^[:space:]#]/{
  /^  -o mynetworks=127\.0\.0\.0\/8,\[::1\]\/128$/d
}' /etc/postfix/master.cf

# postmap(1) reads main.cf; ensure maillog paths are valid before hashing maps.
postconf -e "maillog_file_prefixes = /var, /dev/stdout, /data/logs"
postconf -e "maillog_file = /dev/stdout"

postmap /etc/postfix/generated/virtual_mailbox_maps
postmap /etc/postfix/generated/transport_maps
postmap /etc/postfix/generated/virtual_alias_domains
postmap /etc/postfix/generated/virtual_alias_maps
postmap /etc/postfix/generated/relay_sender_access

postconf -e "maillog_file = /data/logs/postfix.log"
tail -F /data/logs/postfix.log &

watch_maps() {
  local old_sum=""
  while true; do
    local current_sum map_files=()
    map_files=(
      /data/generated/virtual_mailbox_maps
      /data/generated/virtual_mailbox_catchall
      /data/generated/transport_maps
      /data/generated/virtual_alias_domains
      /data/generated/virtual_alias_maps
      /data/generated/relay_sender_access
    )
    shopt -s nullglob
    local cidr_files=(/data/generated/relay_client_access_*.cidr)
    shopt -u nullglob
    current_sum="$(sha256sum "${map_files[@]}" "${cidr_files[@]}" 2>/dev/null | sha256sum | awk '{print $1}')"
    if [[ "${current_sum}" != "${old_sum}" ]]; then
      sync_maps
      postconf -e "maillog_file = /dev/stdout"
      postmap /etc/postfix/generated/virtual_mailbox_maps
      postmap /etc/postfix/generated/transport_maps
      postmap /etc/postfix/generated/virtual_alias_domains
      postmap /etc/postfix/generated/virtual_alias_maps
      postmap /etc/postfix/generated/relay_sender_access
      postconf -e "maillog_file = /data/logs/postfix.log"
      postfix reload || true
      old_sum="${current_sum}"
    fi
    sleep 10
  done
}

watch_maps &

watch_tls_certs() {
  local hostname="${MYHOSTNAME:-}"
  [[ -n "${hostname}" ]] || return 0
  local old_sum=""
  while true; do
    local cert key current_sum paths=()
    mapfile -t paths < <(caddy_cert_paths "${hostname}")
    cert="${paths[0]:-}"
    key="${paths[1]:-}"
    if [[ -f "${cert}" && -f "${key}" ]]; then
      current_sum="$(sha256sum "${cert}" "${key}" | sha256sum | awk '{print $1}')"
      if [[ -n "${old_sum}" && "${current_sum}" != "${old_sum}" ]]; then
        install_smtpd_tls_certs
        postfix reload || true
      fi
      old_sum="${current_sum}"
    fi
    sleep 300
  done
}

watch_tls_certs &
exec postfix start-fg
