FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN mkdir -p /etc/postfix/sasl && \
    apt-get update && apt-get install -y --no-install-recommends \
    postfix \
    rsyslog \
    libsasl2-modules \
    sasl2-bin \
    ca-certificates \
    openssl \
    jq \
    && rm -rf /var/lib/apt/lists/*

COPY infra/postfix/main.cf /etc/postfix/main.cf
COPY infra/postfix/sasl/smtpd.conf /etc/postfix/sasl/smtpd.conf
COPY infra/postfix/parse_postqueue.awk /usr/local/bin/parse_postqueue.awk
COPY infra/postfix/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

EXPOSE 25 587
ENTRYPOINT ["/entrypoint.sh"]
