#!/bin/sh
set -e

if [ -f /mail-exchange-data/generated/caddy.env ]; then
  set -a
  # shellcheck disable=SC1091
  . /mail-exchange-data/generated/caddy.env
  set +a
fi

exec caddy "$@"
