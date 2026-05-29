FROM caddy:2-builder AS builder

RUN xcaddy build \
    --with github.com/caddy-dns/cloudflare

FROM caddy:2-alpine

ENV TZ=Europe/Rome
RUN apk add --no-cache tzdata

COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY infra/caddy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["run", "--config", "/mail-exchange-data/generated/Caddyfile", "--adapter", "caddyfile"]
