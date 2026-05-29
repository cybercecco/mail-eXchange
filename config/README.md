# Configurazione runtime (host)

Directory montata nei container come `/mail-exchange-config`. Modificabile sul server senza rebuild delle immagini.

## Layout

| Percorso | Tipo | Descrizione |
|----------|------|-------------|
| `postfix/main.cf` | statico | `main.cf` Postfix (da `infra/postfix/`) |
| `postfix/sasl/` | statico | SASL Cyrus (`smtpd.conf`) |
| `postfix/generated/` | generato | Mappe virtual/transport/relay (API `regenerate`) |
| `amavis/conf.d/` | statico + runtime | `50-user`, scanner ClamAV, hostname |
| `amavis/spam-overrides.conf` | generato | Override punteggi SpamAssassin (API) |
| `amavis/local-domains.conf` | runtime | Domini locali Amavis (entrypoint da `virtual_alias_domains`) |
| `spamassassin/local.cf` | generato | Regole SpamAssassin (API) |
| `clamav/` | statico (opz.) | Override `clamd.conf` / `freshclam.conf` |

Restano nel volume `mail-data` (`/data`): database SQLite, Caddyfile, OpenDKIM, chiavi DKIM, log, quarantena, SASL relay passwd.

## Sviluppo locale

```bash
./scripts/seed-mail-config.sh
export MAIL_CONFIG_DIR=./config   # opzionale; default in compose
docker compose up -d
```

## Produzione (`/opt/mail-exchange`)

`deploy.sh` crea `config/` e applica i template se mancanti. Variabile opzionale in `.env`:

```env
MAIL_CONFIG_DIR=/opt/mail-exchange/config
```

Dopo modifiche manuali a file statici: `docker compose restart postfix amavis clamav`.

Dopo cambi dal pannello (domini, spam): l'API rigenera i file in `postfix/generated/` e ricarica Postfix/Amavis automaticamente.
