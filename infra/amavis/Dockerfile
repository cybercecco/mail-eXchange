FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Rome

RUN apt-get update && apt-get install -y --no-install-recommends \
    amavisd-new \
    spamassassin \
    clamav \
    libmail-dkim-perl \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY infra/amavis/amavisd.conf /usr/share/mail-exchange/amavis/50-user
COPY infra/amavis/52-clamav-scanner /usr/share/mail-exchange/amavis/52-clamav-scanner
COPY infra/amavis/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

EXPOSE 10024
ENTRYPOINT ["/entrypoint.sh"]
