FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Rome

RUN mkdir -p /etc/postfix/sasl /etc/pam.d && \
    apt-get update && apt-get install -y --no-install-recommends \
    postfix \
    rsyslog \
    libsasl2-modules \
    libsasl2-modules-sql \
    sasl2-bin \
    libpam-modules \
    python3 \
    ca-certificates \
    openssl \
    jq \
    tzdata \
    && rm -rf /var/lib/apt/lists/* && \
    usermod -aG sasl postfix 2>/dev/null || true

COPY infra/postfix/main.cf /usr/share/mail-exchange/postfix/main.cf
COPY infra/postfix/sasl/smtpd.conf /usr/share/mail-exchange/postfix/sasl/smtpd.conf
COPY infra/postfix/pam.d/saslauthd /etc/pam.d/saslauthd
COPY infra/postfix/mx_imap_auth_check.py /usr/local/bin/mx_imap_auth_check.py
COPY infra/postfix/parse_postqueue.awk /usr/local/bin/parse_postqueue.awk
COPY infra/postfix/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh /usr/local/bin/mx_imap_auth_check.py

EXPOSE 25 587
ENTRYPOINT ["/entrypoint.sh"]
