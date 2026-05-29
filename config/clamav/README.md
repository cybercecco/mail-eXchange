# ClamAV (opzionale)

Il servizio `clamav` usa la configurazione predefinita dell'immagine `clamav/clamav:stable`.

Per override, aggiungi qui `clamd.conf` o `freshclam.conf` e monta i file in `docker-compose.yml`, ad esempio:

```yaml
clamav:
  volumes:
    - ${MAIL_CONFIG_DIR:-./config}/clamav/clamd.conf:/etc/clamav/clamd.conf:ro
```
