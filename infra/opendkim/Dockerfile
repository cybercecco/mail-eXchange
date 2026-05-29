FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Rome

RUN apt-get update && apt-get install -y --no-install-recommends \
    opendkim \
    opendkim-tools \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY infra/opendkim/opendkim.conf /etc/opendkim.conf
COPY infra/opendkim/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

EXPOSE 8891
ENTRYPOINT ["/entrypoint.sh"]
