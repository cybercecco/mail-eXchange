#!/usr/bin/env bash
set -euo pipefail

MAIL_CFG="${MAIL_CONFIG_DIR:-/mail-exchange-config}"
SOURCE_GENERATED="${MAIL_CFG}/postfix/generated"
GENERATED_DIR="/etc/postfix/generated"
CHROOT_GENERATED_DIR="/var/spool/postfix/etc/postfix/generated"

MAP_BASENAMES=(
  virtual_mailbox_maps
  virtual_mailbox_catchall
  transport_maps
  transport_catchall
  virtual_alias_domains
  virtual_alias_maps
  relay_sender_access
  relay_restriction_classes
)

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

classify_active_pipeline() {
  local active_out="$1"
  local to_amavis=0 outbound=0 local_q=0
  if printf '%s' "${active_out}" | grep -q "Mail queue is empty"; then
    printf '{"postfix_to_amavis":0,"postfix_outbound":0,"postfix_local":0}'
    return
  fi
  while read -r qid; do
    [[ -n "${qid}" ]] || continue
    local meta relay rl
    meta="$(postcat -qe "${qid}" 2>/dev/null || postcat -q "${qid}" 2>/dev/null || true)"
    relay="$(printf '%s\n' "${meta}" | awk -F= '/^named_attribute: relay=/ {print $2; exit}')"
    rl="$(printf '%s' "${relay}" | tr '[:upper:]' '[:lower:]')"
    if [[ "${rl}" == *"amavis"* || "${rl}" == *":10024"* ]]; then
      to_amavis=$((to_amavis + 1))
    elif [[ -n "${rl}" && "${rl}" != *"127.0.0.1"* ]]; then
      outbound=$((outbound + 1))
    else
      local_q=$((local_q + 1))
    fi
  done < <(printf '%s\n' "${active_out}" | grep -E '^[0-9A-F]+' | awk '{print $1}')
  printf '{"postfix_to_amavis":%s,"postfix_outbound":%s,"postfix_local":%s}' \
    "${to_amavis}" "${outbound}" "${local_q}"
}

write_queue_snapshot() {
  local active=0 deferred=0 hold=0 total=0
  local active_out deferred_out hold_out
  local active_messages deferred_messages hold_messages pipeline_json
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
  pipeline_json="$(classify_active_pipeline "${active_out}")"
  mkdir -p /data/stats
  cat > /data/stats/queue.json.tmp <<EOF
{"total":${total},"active":${active},"deferred":${deferred},"hold":${hold},"updated_at":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","pipeline":${pipeline_json},"messages":{"active":${active_messages},"deferred":${deferred_messages},"hold":${hold_messages}}}
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

ensure_postfix_config() {
  mkdir -p /etc/postfix/sasl "${SOURCE_GENERATED}"
  if [[ -f "${MAIL_CFG}/postfix/main.cf" ]]; then
    cp -f "${MAIL_CFG}/postfix/main.cf" /etc/postfix/main.cf
  elif [[ ! -f /etc/postfix/main.cf && -f /usr/share/mail-exchange/postfix/main.cf ]]; then
    cp /usr/share/mail-exchange/postfix/main.cf /etc/postfix/main.cf
  fi
  if [[ -f "${MAIL_CFG}/postfix/sasl/smtpd.conf" ]]; then
    cp -f "${MAIL_CFG}/postfix/sasl/smtpd.conf" /etc/postfix/sasl/smtpd.conf
  elif [[ ! -f /etc/postfix/sasl/smtpd.conf && -f /usr/share/mail-exchange/postfix/sasl/smtpd.conf ]]; then
    cp /usr/share/mail-exchange/postfix/sasl/smtpd.conf /etc/postfix/sasl/smtpd.conf
  fi
}

mkdir -p "${SOURCE_GENERATED}" "${GENERATED_DIR}" "${CHROOT_GENERATED_DIR}"
mkdir -p /data/logs /data/stats
touch /data/logs/postfix.log
ensure_postfix_config
watch_queue &

for map_name in "${MAP_BASENAMES[@]}"; do
  touch "${SOURCE_GENERATED}/${map_name}"
done

# Chrooted smtp(8) needs Docker DNS to reach amavis/opendkim by service name.
mkdir -p /var/spool/postfix/etc
cp -f /etc/resolv.conf /var/spool/postfix/etc/resolv.conf

disable_smtpd_chroot() {
  # Container: avoid chroot + external map volume (proxymap/symlink edge cases).
  sed -i '/^smtp[[:space:]]\+inet/s/\([[:space:]]\)y\([[:space:]]\+-[[:space:]]\+-[[:space:]]\+smtpd\)/\1n\2/' /etc/postfix/master.cf
  sed -i '/^submission[[:space:]]\+inet/s/\([[:space:]]\)y\([[:space:]]\+-[[:space:]]\+-[[:space:]]\+smtpd\)/\1n\2/' /etc/postfix/master.cf
}

sync_generated_maps() {
  mkdir -p "${SOURCE_GENERATED}" "${GENERATED_DIR}" "${CHROOT_GENERATED_DIR}"
  for map_name in "${MAP_BASENAMES[@]}"; do
    if [[ -f "${SOURCE_GENERATED}/${map_name}" ]]; then
      cp -f "${SOURCE_GENERATED}/${map_name}" "${GENERATED_DIR}/${map_name}"
    fi
  done
  shopt -s nullglob
  for cidr in "${SOURCE_GENERATED}"/relay_client_access_*.cidr; do
    cp -f "${cidr}" "${GENERATED_DIR}/$(basename "${cidr}")"
  done
  shopt -u nullglob

  postconf -e "maillog_file = /dev/stdout"
  postmap "${GENERATED_DIR}/virtual_mailbox_maps" 2>/dev/null || true
  postmap "${GENERATED_DIR}/transport_maps" 2>/dev/null || true
  postmap "${GENERATED_DIR}/virtual_alias_domains" 2>/dev/null || true
  postmap "${GENERATED_DIR}/virtual_alias_maps" 2>/dev/null || true
  postmap "${GENERATED_DIR}/relay_sender_access" 2>/dev/null || true
  apply_relay_restriction_classes
  apply_relay_mynetworks
  postconf -e "maillog_file = /data/logs/postfix.log"

  for map_name in "${MAP_BASENAMES[@]}"; do
    [[ -f "${GENERATED_DIR}/${map_name}" ]] || continue
    cp -f "${GENERATED_DIR}/${map_name}" "${CHROOT_GENERATED_DIR}/${map_name}"
  done
  shopt -s nullglob
  for map_file in "${GENERATED_DIR}"/*.db "${GENERATED_DIR}"/relay_client_access_*.cidr; do
    cp -f "${map_file}" "${CHROOT_GENERATED_DIR}/$(basename "${map_file}")"
  done
  shopt -u nullglob
}

reload_postfix_maps() {
  sync_generated_maps
  apply_relay_restriction_classes
  apply_relay_mynetworks
  postfix reload 2>/dev/null || true
}

apply_relay_restriction_classes() {
  local classes_file="${GENERATED_DIR}/relay_restriction_classes"
  if [[ ! -f "${classes_file}" ]]; then
    postconf -X smtpd_restriction_classes 2>/dev/null || true
    return 0
  fi
  local classes=""
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%%#*}"
    line="$(printf '%s' "${line}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -n "${line}" ]] || continue
    if [[ "${line}" == smtpd_restriction_classes* ]]; then
      classes="${line#smtpd_restriction_classes}"
      classes="$(printf '%s' "${classes}" | sed 's/^[[:space:]]*//')"
      continue
    fi
    local class_name="${line%% *}"
    local class_rules="${line#${class_name}}"
    class_rules="$(printf '%s' "${class_rules}" | sed 's/^[[:space:]]*//')"
    [[ -n "${class_name}" && -n "${class_rules}" ]] || continue
    # check_client_access OK stops the class; final reject denies non-listed relay clients.
    if [[ "${class_rules}" != *reject* ]]; then
      class_rules="${class_rules}, reject"
    fi
    postconf -e "${class_name} = ${class_rules}"
  done < "${classes_file}"
  if [[ -n "${classes}" ]]; then
    postconf -e "smtpd_restriction_classes = ${classes}"
  else
    postconf -X smtpd_restriction_classes 2>/dev/null || true
  fi
}

apply_relay_mynetworks() {
  local relay_cidr="${GENERATED_DIR}/relay_mynetworks.cidr"
  local base="${MYNETWORKS:-127.0.0.0/8 [::1]/128}"
  if [[ ! -f "${relay_cidr}" ]]; then
    shopt -s nullglob
    local cidr_files=("${GENERATED_DIR}"/relay_client_access_*.cidr)
    if [[ ${#cidr_files[@]} -gt 0 ]]; then
      awk '!/^[[:space:]]*#/ && NF {print $1 "\tOK"}' "${cidr_files[@]}" | sort -u > "${relay_cidr}.tmp"
      mv "${relay_cidr}.tmp" "${relay_cidr}"
    fi
    shopt -u nullglob
  fi
  if [[ -f "${relay_cidr}" ]] && grep -qv '^[[:space:]]*#' "${relay_cidr}" 2>/dev/null; then
    postconf -e "mynetworks = ${base}, cidr:${relay_cidr}"
  else
    postconf -e "mynetworks = ${base}"
  fi
}

if [[ -n "${MYHOSTNAME:-}" ]]; then
  postconf -e "myhostname = ${MYHOSTNAME}"
fi
apply_relay_mynetworks
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

submission inet n       -       n       -       -       smtpd
  -o syslog_name=postfix/submission
  -o smtpd_tls_security_level=encrypt
  -o smtpd_sasl_auth_enable=yes
  -o smtpd_tls_auth_only=yes
  -o smtpd_recipient_restrictions=permit_mynetworks,reject_unauth_destination,reject_non_fqdn_recipient,reject_unknown_recipient_domain
EOF
}

configure_submission
disable_smtpd_chroot

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

postconf -e "maillog_file_prefixes = /var, /dev/stdout, /data/logs"
postconf -e "maillog_file = /data/logs/postfix.log"
tail -F /data/logs/postfix.log &

watch_maps() {
  local old_sum=""
  while true; do
    local current_sum map_files=()
    map_files=(
      "${SOURCE_GENERATED}/virtual_mailbox_maps"
      "${SOURCE_GENERATED}/virtual_mailbox_catchall"
      "${SOURCE_GENERATED}/transport_maps"
      "${SOURCE_GENERATED}/transport_catchall"
      "${SOURCE_GENERATED}/virtual_alias_domains"
      "${SOURCE_GENERATED}/virtual_alias_maps"
      "${SOURCE_GENERATED}/relay_sender_access"
      "${SOURCE_GENERATED}/relay_restriction_classes"
      "${SOURCE_GENERATED}/relay_mynetworks.cidr"
    )
    shopt -s nullglob
    local cidr_files=("${SOURCE_GENERATED}"/relay_client_access_*.cidr)
    shopt -u nullglob
    current_sum="$(sha256sum "${map_files[@]}" "${cidr_files[@]}" 2>/dev/null | sha256sum | awk '{print $1}')"
    if [[ "${current_sum}" != "${old_sum}" ]]; then
      reload_postfix_maps
      old_sum="${current_sum}"
    fi
    sleep 10
  done
}

startup_map_bootstrap() {
  local attempt=0
  sleep 5
  while [[ "${attempt}" -lt 18 ]]; do
    reload_postfix_maps
    attempt=$((attempt + 1))
    sleep 5
  done
}

sync_generated_maps
startup_map_bootstrap &
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
