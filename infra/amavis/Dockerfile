FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    amavisd-new \
    spamassassin \
    clamav \
    libmail-dkim-perl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY infra/amavis/amavisd.conf /etc/amavis/conf.d/50-user
COPY infra/amavis/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

EXPOSE 10024
ENTRYPOINT ["/entrypoint.sh"]
