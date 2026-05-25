# Mail-eXchange

**IT** — Piattaforma Docker per relay SMTP multi-dominio con pannello web di gestione, antispam/antivirus, firma DKIM e sincronizzazione cluster.

**EN** — Docker-based multi-domain SMTP relay platform with web control plane, antispam/antivirus, DKIM signing, and cluster sync.

Repository: [github.com/cybercecco/mail-eXchange](https://github.com/cybercecco/mail-eXchange)

---

## Indice / Table of contents

1. [Panoramica / Overview](#panoramica--overview)
2. [Stack tecnologico](#stack-tecnologico--technology-stack)
3. [Architettura multi-server](#architettura-multi-server--multi-server-architecture)
4. [Interfaccia web](#interfaccia-web--web-ui)
5. [Gestione domini](#gestione-domini--domain-management)
6. [Caselle e import CSV](#caselle-e-import-csv--mailboxes--csv-import)
7. [Sincronizzazione cluster](#sincronizzazione-cluster--cluster-sync)
8. [Relay in uscita (SASL + IP per dominio)](#relay-in-uscita-sasl--ip-per-dominio--outbound-relay)
9. [Configurazione piattaforma](#configurazione-piattaforma--platform-settings)
10. [Traffico e code Postfix](#traffico-e-code-postfix--traffic--queues)
11. [Stato sistema e riavvio servizi](#stato-sistema-e-riavvio-servizi--system-status)
12. [Utenti, MFA e notifiche errori](#utenti-mfa-e-notifiche-errori--users-mfa--error-alerts)
13. [DNS, SpamAssassin e DKIM](#dns-spamassassin-e-dkim)
14. [Immagini Docker Hub](#immagini-docker-hub--docker-hub-images)
15. [Deploy e produzione](#deploy-e-produzione--deployment)
16. [Schema database](#schema-database--database-schema)
17. [API REST](#api-rest)
18. [Variabili d'ambiente](#variabili-dambiente--environment-variables)
19. [Avvio rapido](#avvio-rapido--quick-start)
20. [Note operative](#note-operative--operational-notes)

---

## Panoramica / Overview

Mail-eXchange è un **control plane** per infrastrutture mail basate su Postfix. Ogni dominio gestito può:

- ricevere posta in ingresso (MX) e inoltrarla a server downstream (IP/FQDN + porta);
- definire caselle con routing per-indirizzo (`utente@dominio → smtp:[host]:porta`);
- attivare **catch-all relay** (tutta la posta `@dominio` verso il primo server destinazione);
- firmare la posta in uscita con **DKIM** (selector configurabile per dominio);
- autorizzare **relay SMTP in uscita** via SASL (porta 587) o via IP sorgente per dominio;
- replicare l'elenco caselle verso un **nodo cluster** fratello.

La configurazione è persistita in **SQLite** e rigenerata automaticamente in file Postfix, OpenDKIM, Amavis, SpamAssassin e Caddy.

---

## Stack tecnologico / Technology stack

| Componente | Ruolo |
|------------|-------|
| **Postfix** | MTA primario: ricezione MX, relay, submission (587), transport maps per casella |
| **Amavis** | Content filter: integrazione SpamAssassin + ClamAV |
| **ClamAV** | Antivirus (`clamav/clamav:stable`, immagine upstream) |
| **OpenDKIM** | Milter per firma DKIM in uscita, chiavi per dominio |
| **Caddy** | Reverse proxy HTTPS per la UI; certificato interno o LE DNS-01 Cloudflare |
| **FastAPI** | API backend, auth JWT, rigenerazione config, operazioni coda |
| **React** | Frontend SPA (Vite), tema chiaro/scuro |

Tutti i servizi custom sono orchestrati con **Docker Compose** sulla rete interna `mxnet`.

---

## Architettura multi-server / Multi-server architecture

```
                    ┌─────────────────────────────────────┐
                    │  Nodo A (mx1.example.com)           │
                    │  Caddy :60443 → React + FastAPI     │
                    │  Postfix :25 / :587                 │
                    │  Amavis → ClamAV, OpenDKIM          │
                    └──────────────┬──────────────────────┘
                                   │ HTTPS :60443
                                   │ POST /api/sync/domain-bundle
                                   │ Bearer sync_secret (per dominio)
                    ┌──────────────▼──────────────────────┐
                    │  Nodo B (mx2.example.com)           │
                    │  (stessa stack, DB indipendente)    │
                    └─────────────────────────────────────┘
```

- Ogni nodo ha **database SQLite proprio** e configurazione locale (dominio, destinazioni, impostazioni).
- La sync cluster replica **caselle**, **selector/chiavi DKIM** e **suggerimenti MX** di un dominio verso il peer configurato in `sibling_fqdn`.
- Il nodo che modifica i dati è **fonte di verità**; il push sovrascrive caselle e allinea DKIM sul peer.
- `sibling_fqdn`, destinazioni, relay e impostazioni di sistema **non** vengono sincronizzate.
- I record MX inviati descrivono gli host SMTP del nodo sorgente (`POSTFIX_HOSTNAME`, `PUBLIC_HOSTNAME`); il peer li accumula in `dns_mx_hints` per la verifica DNS.
- Porta HTTPS predefinita per sync: **60443** (`SYNC_HTTPS_PORT`).

Per produzione multi-nodo tipico:

1. Due (o più) server con stack identica; per ogni dominio in cluster, stessa **chiave precondivisa sync** e `sibling_fqdn` configurati nel tab **Cluster** su entrambi i nodi.
2. Su dominio `example.com` su nodo A: `sibling_fqdn = mx2.example.com`.
3. Su nodo B: configurazione speculare o lasciare vuoto se la sync è unidirezionale.

---

## Interfaccia web / Web UI

Navigazione principale (sidebar):

| Sezione | Voci |
|---------|------|
| **Mail** | Domini |
| **Sicurezza** | DNS (SPF/DKIM/DMARC), SpamAssassin |
| **Sistema** | Traffico, Il mio account, Stato & sessione |
| **Configurazione** *(solo admin)* | Sistema & test mail, Utenti |

Accesso: `https://<hostname>:60443` (o HTTP su `:60080`).

---

## Gestione domini / Domain management

La pagina **Domini** usa **tab orizzontali** — una per ogni dominio configurato. Badge sul tab: `Off`, `Cluster`, `Relay`, `IP relay`.

Ogni dominio ha **sotto-tab**:

### Generale

- Nome dominio, selector DKIM, conteggio caselle, stato abilitato/disabilitato.
- Abilita / disabilita / elimina dominio (eliminazione solo senza caselle).
- **Inoltro catch-all**: checkbox *«Inoltra tutta la posta in ingresso al server destinazione»*.
  - Se attivo, qualsiasi indirizzo `@dominio` viene instradato al **primo** server destinazione configurato, anche senza casella esplicita.
  - Richiede dominio abilitato e almeno una destinazione.

### Destinazioni

- Elenco server SMTP downstream (label, host, porta).
- CRUD destinazioni; modifica host/porta aggiorna in blocco le caselle che usavano quella destinazione.
- Le caselle devono riferire una destinazione già definita per il dominio.

### Caselle

- CRUD caselle email → destinazione (host:porta).
- **Import CSV** (vedi sezione dedicata).
- Filtro per dominio attivo.

### Cluster

- Campo **FQDN Server Cluster** (`sibling_fqdn`): peer che riceve push HTTPS del bundle dominio.
- Campo **Chiave precondivisa sync** (`sync_secret`): stesso valore su entrambi i nodi per quel dominio (non esposta in lista API; solo `sync_secret_configured: true/false`).
- Sync automatica dopo: salvataggio cluster, creazione/modifica/eliminazione casella, import CSV, cambio selector DKIM, rigenerazione chiavi DKIM (`POST /api/domains/{id}/dkim/regenerate`).
- Sul peer: caselle allineate, `dkim_selector` e chiavi OpenDKIM installate (volume condiviso), suggerimenti MX salvati per il sotto-tab DNS.
- Avviso `sync_warning` in UI se il peer non risponde (salvataggio locale comunque valido).

### Relay

- **IP/CIDR relay in uscita** per dominio (`relay_source_ips`, max 64 voci).
- Policy Postfix: relay consentito se client autenticato SASL **oppure** IP sorgente in lista per envelope `@dominio`.
- Non influisce sulla consegna MX inbound verso domini virtuali locali.

### DKIM

- Selector per dominio (default `mail`).
- Chiavi generate in volume OpenDKIM; record TXT pubblicato in `<selector>._domainkey.<dominio>`.
- Verifica DNS disponibile nella sezione **DNS**.

---

## Caselle e import CSV / Mailboxes & CSV import

Formato CSV supportato:

- Delimitatore `,` o `;` (auto-rilevato).
- Colonne (header): `mail`/`email` oppure `local`+`domain`, più `destination_label`/`label`/`destinazione` (etichetta server in Domini, **non** host).
- Senza intestazione: ordine `mail`, `destination_label` (due colonne). Tre colonne con porta numerica = formato legacy host/porta (deprecato).
- Opzioni import:
  - **Salta prima riga** (intestazione).
  - **Aggiorna caselle esistenti** (stessa email).

Esempio:

```csv
mail,destination_label
user@example.com,Primary
admin@example.com,Backup MX
```

All'import l'etichetta viene risolta sulla destinazione **locale** del dominio (`host`/`port` del nodo). Requisiti: dominio abilitato; etichetta presente in `domain_destinations` per quel dominio.

Formato legacy (deprecato): `mail`, `destination_host`/`host`, `porta` opzionale.

---

## Sincronizzazione cluster / Cluster sync

| Impostazione | Descrizione |
|--------------|-------------|
| `sync_secret` (per dominio) | Chiave Bearer condivisa tra i nodi per quel dominio (tab Cluster) |
| `SYNC_TLS_VERIFY` | Verifica certificato TLS peer (default `true`) |
| `SYNC_HTTPS_PORT` | Porta HTTPS per push (default `60443`) |
| `PUBLIC_HOSTNAME` | Evita sync verso se stessi |

> **Deprecato:** `SYNC_SHARED_SECRET` in `.env` è ancora accettato come fallback con warning in log, ma va sostituito con `sync_secret` per dominio.

**Endpoint ricevente:** `POST /api/sync/domain-bundle` (autenticazione Bearer). L'endpoint legacy `POST /api/sync/mailboxes` accetta lo stesso payload.

**Payload:**

```json
{
  "domain_name": "example.com",
  "mailboxes": [
    {
      "email": "user@example.com",
      "destination_host": "backend.example.com",
      "destination_port": 25,
      "enabled": true
    }
  ],
  "domain_sync": {
    "dkim_selector": "mail",
    "dkim_private_key_pem": "-----BEGIN PRIVATE KEY-----...",
    "dkim_public_key_dns_txt": "v=DKIM1; k=rsa; p=..."
  },
  "mx_records": [
    { "priority": 10, "host": "mx1.example.com" },
    { "priority": 20, "host": "smtp.example.com" }
  ]
}
```

Il ricevente crea il dominio se assente, upsert/delete caselle, aggiorna `dkim_selector` (senza toccare `sibling_fqdn`), installa le chiavi DKIM nel volume OpenDKIM, unisce `mx_records` in `dns_mx_hints` e rigenera Postfix/OpenDKIM. Destinazioni, relay e impostazioni dominio restano locali.

**MX:** ogni nodo esporta i propri hostname SMTP; il peer non modifica il DNS pubblico ma conserva gli hint per evidenziare in UI i record MX mancanti in un cluster multi-nodo.

---

## Relay in uscita (SASL + IP per dominio) / Outbound relay

Postfix submission (**porta 587**, mappata con `SUBMISSION_PUBLISHED_PORT`):

- **SASL** (PLAIN/LOGIN via `sasldb`) — autenticazione client per relay generico.
- **IP per dominio** — tab Relay nel dominio: lista CIDR; envelope sender `@dominio` autorizza relay da quegli IP senza SASL.

Restrizioni (`smtpd_relay_restrictions`):

1. `permit_mynetworks`
2. `permit_sasl_authenticated`
3. `check_sender_access` (mappe CIDR per dominio)
4. `reject`

---

## Configurazione piattaforma / Platform settings

Pagina **Configurazione → Sistema & test mail** (solo admin):

| Campo | Effetto |
|-------|---------|
| **URL pubblico** | Hostname Caddy/TLS (es. `smtp.example.com`) |
| **Email ACME** | Contatto Let's Encrypt |
| **DNS container** | Resolver Docker per tutti i servizi stack |

Il **token Cloudflare** per la sfida DNS-01 ACME va in `CLOUDFLARE_API_TOKEN` nel file `.env` (non in UI né DB). Dopo modifica: `docker compose up -d` (o `./deploy.sh`) e riavvio Caddy.

**Apply on save:** al salvataggio l'API rigenera `Caddyfile`, `docker-dns.override.yml`, ricrea i container con nuovi DNS e riavvia Caddy.

Funzioni aggiuntive:

- **Test mail** — invio di prova da casella selezionata.
- **Anteprima errori / invio digest** — report guasti (vedi notifiche).

---

## Traffico e code Postfix / Traffic & queues

Pagina **Sistema → Traffico**:

- **Finestra temporale**: 15 min, 1 h, 6 h, 24 h (o custom 5–1440 min).
- Metriche: ingresso, in coda (antispam/AV), bloccate, in uscita.
- **Snapshot code in tempo reale** (poll ogni 5 s): attive, differite, in hold.
- Dettaglio coda con contenuto messaggio.

Azioni admin sulle code:

| Azione | Descrizione |
|--------|-------------|
| Flush | Forza invio messaggi selezionati |
| Delete | Elimina messaggi dalla coda |
| Hold all | Mette in hold tutta la coda |
| Release all | Rilascia messaggi in hold |
| Pause / Resume | Pausa/riprende Postfix |

---

## Stato sistema e riavvio servizi / System status

Pagina **Sistema → Stato & sessione**:

- Chip stato per ogni demone: API, Frontend, Caddy, Postfix, Amavis, ClamAV, OpenDKIM.
- Probe TCP/HTTP/clamd PING dalla rete Docker.
- **Riavvio** singolo container (admin); avviso se si riavvia l'API (sessione interrotta).
- Health check API pubblico: `GET /api/health`.

---

## Utenti, MFA e notifiche errori / Users, MFA & error alerts

### Autenticazione

- Login username/password (bcrypt, SQLite).
- **MFA TOTP** opzionale (Google Authenticator, ecc.).
- JWT in `sessionStorage`; header `Authorization: Bearer`.
- Bootstrap primo admin se tabella `users` vuota (`ADMIN_USERNAME` / `ADMIN_PASSWORD`).

### Ruoli

| Ruolo | Permessi |
|-------|----------|
| **admin** | Tutto: domini, caselle, utenti, impostazioni, code, riavvio |
| **user** | Lettura/gestione mail e sicurezza; no utenti né config sistema |

### Notifiche errori (solo guasti)

- Worker in background ogni **15 min**; finestra analisi **30 min** (`ERROR_WINDOW_MINUTES`).
- Pattern: error/fatal/reject, bounce/defer, SPAM/INFECT blocked, errori ClamAV/OpenDKIM.
- Email agli utenti con `notify_email` configurato (profilo account).
- Digest inviato solo se fingerprint errori **cambiata** (no spam duplicati).
- Esclusi warning generici Postfix/Amavis.

---

## DNS, SpamAssassin e DKIM

### DNS (SPF / DKIM / DMARC)

Verifica automatica per tutti i domini o singolo dominio (`?domain=`):

- Record MX verso `POSTFIX_HOSTNAME`
- A/AAAA del hostname pubblico
- SPF sul dominio
- DKIM: confronto TXT DNS vs chiave locale
- DMARC su `_dmarc.<dominio>` (consigliato)

### SpamAssassin

Policy **globali** per tutto lo stack:

- Whitelist / blacklist mittenti
- Override punteggi regole

Rigenerazione `spamassassin.local.cf` e override Amavis (`amavis-spam-overrides.conf` con `@score_sender_maps` per la whitelist). Amavis ricarica la configurazione automaticamente quando i file generati cambiano.

Messaggi spam/virus bloccati fino al livello *kill* vengono **messi in quarantena** (non eliminati subito): Amavis salva in `/data/quarantine/incoming/`, l'API indicizza in `/data/quarantine/{id}/` con TTL **36 ore**, poi cancellazione automatica. Dal pannello **Sicurezza → Quarantena spam** (solo admin) è possibile cercare, rilasciare verso il destinatario originale o eliminare manualmente.

---

## Immagini Docker Hub / Docker Hub images

Namespace: **`cybercecco`** (repository in minuscolo su Hub).

| Servizio | Immagine |
|----------|----------|
| API | `cybercecco/mail-exchange-api` |
| Frontend | `cybercecco/mail-exchange-frontend` |
| Caddy | `cybercecco/mail-exchange-caddy` |
| Postfix | `cybercecco/mail-exchange-postfix` |
| Amavis | `cybercecco/mail-exchange-amavis` |
| OpenDKIM | `cybercecco/mail-exchange-opendkim` |
| ClamAV | `clamav/clamav:stable` (upstream) |

Tag: `latest` e opzionalmente short SHA git (es. `a1b2c3d`).

### Build e push (maintainer)

```bash
docker login   # non committare credenziali
./scripts/build-and-push.sh
```

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `DOCKERHUB_NAMESPACE` | utente `docker login` | Namespace Hub |
| `MAIL_EXCHANGE_IMAGE_TAG` | `latest` | Tag principale |
| `PUSH` | `1` | `0` = solo build locale |

---

## Deploy e produzione / Deployment

### Build e push (macchina di sviluppo)

Dopo modifiche al codice, buildare e pubblicare le immagini su Docker Hub:

```bash
docker login
./scripts/build-and-push.sh
```

### Deploy da Docker Hub (pull-only, senza build)

Il server di produzione **non** contiene sorgenti: solo `docker-compose.yml`, `.env` e opzionalmente `docker-dns.override.yml`. I volumi Docker (`mail-data`, ecc.) restano intatti.

```bash
cp .env.example .env   # oppure .env.production sul server
# Impostare DOCKERHUB_NAMESPACE=cybercecco e MAIL_EXCHANGE_IMAGE_TAG=latest

docker compose pull
docker compose up -d
```

Porte predefinite:

| Porta host | Servizio |
|------------|----------|
| **60080** | Caddy HTTP (UI; senza `CLOUDFLARE_API_TOKEN` non reindirizza a HTTPS) |
| **60443** | Caddy HTTPS (UI) |
| **25** / **587** | Postfix SMTP / submission (`SMTP_PUBLISHED_PORT`, `SUBMISSION_PUBLISHED_PORT`) |

Sovrascrivibili con `CADDY_HTTP_PORT`, `CADDY_HTTPS_PORT` in `.env`.

### Deploy remoto SSH

```bash
# Nodo primario (default se ometti argomenti)
./deploy.sh root@172.22.11.125 /opt/mail-exchange

# Secondo nodo / cluster (stessa directory sotto /opt/)
./deploy.sh root@192.168.1.69 /opt/mail-exchange .env.production.secondary
```

Richiede un file env locale (`.env.production` per il nodo A, oppure terzo argomento / `DEPLOY_ENV_FILE` per il nodo B). Copia solo `docker-compose.yml` e `.env` sul server, rimuove sorgenti già presenti (`api/`, `frontend/`, `infra/`, ecc.), esegue `docker compose pull && docker compose up -d` (nessun build), applica override DNS generati dall'API.

#### Bootstrap nodo B (TLS e Cloudflare)

1. Creare `.env.production.secondary` da `.env.production.secondary.example` con `POSTFIX_HOSTNAME`, `PUBLIC_HOSTNAME` e `CADDY_DOMAIN` = FQDN del nodo (es. `smtp.vetrobalsamo.com`). Configurare la chiave sync per dominio nel tab **Cluster** su entrambi i nodi.
2. Deploy: `./deploy.sh root@192.168.1.69 /opt/mail-exchange .env.production.secondary`
3. Aprire **`http://<fqdn-o-ip>:60080`** (es. `http://192.168.1.69:60080`) — la UI è raggiungibile senza certificato valido.
4. Login admin → **Configurazione** → verificare **URL pubblico** = FQDN del nodo.
5. Impostare **`CLOUDFLARE_API_TOKEN`** nel `.env` del server (permesso DNS Edit sulla zona Cloudflare), poi `docker compose up -d` (o redeploy). Caddy rigenera il `Caddyfile` all'avvio API e ottiene Let's Encrypt via DNS-01.
6. Fino al passo 5, HTTPS su `:60443` usa un certificato interno (avviso nel browser, accettabile per bootstrap).

**Rinnovo certificati:** Caddy conserva i certificati nel volume `caddy-data` (`/data` nel container) e rinnova automaticamente i certificati Let's Encrypt circa **30 giorni prima della scadenza** (comportamento predefinito di Caddy).

### DNS minimo (per dominio)

- MX → `POSTFIX_HOSTNAME`
- A/AAAA di `POSTFIX_HOSTNAME` (o `PUBLIC_HOSTNAME` / `PUBLIC_IPV4` per suggerimenti)
- SPF che autorizza il server
- DKIM: `<selector>._domainkey.<dominio>` TXT
- DMARC consigliato

---

## Schema database / Database schema

```sql
domains (
  id, name UNIQUE, enabled, dkim_selector, sibling_fqdn, sync_secret,
  relay_all_inbound, relay_source_ips, dns_mx_hints,
  updated_at, created_at
)

domain_destinations (
  id, domain_id FK, label, host, port,
  UNIQUE(domain_id, host, port)
)

mailboxes (
  id, email UNIQUE, destination_host, destination_port,
  enabled, domain_id FK
)

users (
  id, username UNIQUE, password_hash, role,
  totp_secret, mfa_enabled, notify_email, created_at
)

spam_settings (id=1, json_payload)
system_settings (id=1, json_payload)
```

Volume Docker `mail-data` → `/data` (DB, log, file generati, chiavi DKIM pubbliche).

---

## API REST

Tutte le route di gestione richiedono JWT, tranne `/api/health`, `/api/auth/login`, `/api/auth/mfa/verify`, `/api/auth/logout`.

### Auth

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| POST | `/api/auth/login` | Login → JWT o `mfa_required` |
| POST | `/api/auth/mfa/verify` | Completa login TOTP |
| GET | `/api/auth/me` | Profilo corrente |
| POST | `/api/auth/password` | Cambio password |
| PUT | `/api/auth/profile/notify-email` | Email notifiche guasti |
| POST | `/api/auth/mfa/setup` | Genera secret + QR |
| POST | `/api/auth/mfa/confirm` | Attiva MFA |
| POST | `/api/auth/mfa/disable` | Disattiva MFA |

### Domini e caselle

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET/POST | `/api/domains` | Elenco / crea |
| PUT/DELETE | `/api/domains/{id}` | Aggiorna / elimina |
| GET/POST | `/api/domains/{id}/destinations` | Destinazioni |
| PUT/DELETE | `/api/domains/{id}/destinations/{dest_id}` | Modifica / elimina destinazione |
| GET/POST/PUT/DELETE | `/api/mailboxes` | CRUD caselle |
| POST | `/api/mailboxes/import` | Import CSV |

### Sistema

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET/PUT | `/api/settings` | Impostazioni piattaforma |
| POST | `/api/settings/test-mail` | Invio test |
| GET | `/api/system/daemons` | Stato servizi |
| POST | `/api/system/daemons/{id}/restart` | Riavvio container |
| GET | `/api/stats/traffic` | Statistiche traffico |
| GET | `/api/stats/queue` | Dettaglio coda |
| GET | `/api/stats/queue/snapshot` | Snapshot code live |
| POST | `/api/stats/queue/{flush,delete,hold,release,pause,resume}` | Operazioni coda |
| GET | `/api/notifications/errors/preview` | Anteprima guasti 30 min |
| POST | `/api/notifications/errors/send` | Invio digest |
| POST | `/api/sync/domain-bundle` | Sync cluster bundle (Bearer secret) |
| POST | `/api/sync/mailboxes` | Alias legacy sync cluster |
| POST | `/api/domains/{id}/dkim/regenerate` | Rigenera chiavi DKIM (+ push cluster) |

### Sicurezza e DNS

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET/PUT | `/api/spamassassin` | Policy antispam |
| GET | `/api/quarantine?from=&to=&q=` | Elenco quarantena (admin) |
| GET | `/api/quarantine/{id}` | Dettaglio messaggio quarantena |
| POST | `/api/quarantine/{id}/release` | Rilascio verso destinatario |
| DELETE | `/api/quarantine/{id}` | Eliminazione manuale |
| GET | `/api/dns/check` | Verifica DNS tutti i domini |
| GET | `/api/dns/check?domain=` | Singolo dominio |
| GET/POST/PUT/DELETE | `/api/users` | Gestione utenti (admin) |

---

## Variabili d'ambiente / Environment variables

### Mail e rete

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `MAIL_DOMAIN` | `example.com` | Fallback migrazione single-domain; bootstrap legacy |
| `POSTFIX_HOSTNAME` | — | Hostname SMTP/MX del server |
| `PUBLIC_HOSTNAME` | — | Hostname pubblico alternativo (DNS/sync) |
| `PUBLIC_IPV4` | — | IP pubblici forzati per suggerimenti SPF (comma-separated) |
| `CADDY_DOMAIN` | = POSTFIX | Hostname iniziale URL pubblico |
| `CADDY_HTTP_PORT` | `60080` | Porta host HTTP Caddy |
| `CADDY_HTTPS_PORT` | `60443` | Porta host HTTPS Caddy |
| `ACME_EMAIL` | — | Email Let's Encrypt |
| `CLOUDFLARE_API_TOKEN` | — | Token API Cloudflare (DNS Edit) per certificati LE via Caddy DNS-01; solo in `.env`, mai in UI/DB |
| `MYNETWORKS` | reti Docker | Reti trusted Postfix |
| `DKIM_SELECTOR` | `mail` | Selector DKIM default |
| `SMTP_PUBLISHED_PORT` | `2525` | Porta host SMTP (→ 25 container) |
| `SUBMISSION_PUBLISHED_PORT` | `1587` | Porta host submission (→ 587) |
| `POSTFIX_SMTP_TLS` | `false` | TLS API→Postfix interno |
| `DOCKER_DNS_SERVERS` | OpenDNS | Resolver default container |

### Autenticazione UI

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `JWT_SECRET` | — | **Obbligatorio in produzione** |
| `JWT_EXPIRE_MINUTES` | `480` | Durata token JWT |
| `ADMIN_USERNAME` | — | Bootstrap primo admin |
| `ADMIN_PASSWORD` | — | Password bootstrap (solo se DB vuoto) |
| `MFA_ISSUER` | `Mail Exchange` | Nome app authenticator |

### Sync cluster

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `SYNC_TLS_VERIFY` | `true` | Verifica cert TLS peer |
| `SYNC_HTTPS_PORT` | `60443` | Porta HTTPS sync |

La chiave Bearer (`sync_secret`) si configura per dominio nel tab Cluster, non in `.env`.

### Docker Hub deploy

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `DOCKERHUB_NAMESPACE` | — | Es. `cybercecco` |
| `MAIL_EXCHANGE_IMAGE_TAG` | `latest` | Tag immagini Hub |

### API container (avanzate)

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `DATA_DIR` | `/data` | Directory dati |
| `API_PORT` | `8000` | Porta API interna |
| `COMPOSE_PROJECT_DIR` | `/compose` | Mount compose per apply DNS |
| `COMPOSE_PROJECT_NAME` | auto | Nome progetto Docker Compose |

> **Sicurezza:** non committare `.env`, `.env.production` o token reali. Usare `.env.example` come template.

---

## Avvio rapido / Quick start

```bash
git clone https://github.com/cybercecco/mail-eXchange.git
cd mail-eXchange
cp .env.example .env
# Modificare JWT_SECRET, POSTFIX_HOSTNAME, ADMIN_* ...

docker compose pull
docker compose up -d
# Sviluppo con build locale: ./scripts/build-and-push.sh (PUSH=0)
```

1. Apri `http://localhost:60080` o `https://<host>:60443`.
2. Accedi con credenziali admin bootstrap.
3. **Domini** → aggiungi dominio → tab Destinazioni → tab Caselle.
4. (Consigliato) **Il mio account** → MFA + cambio password.
5. **Configurazione** → URL pubblico; per TLS LE impostare `CLOUDFLARE_API_TOKEN` in `.env`.

---

## Note operative / Operational notes

- `MAIL_DOMAIN` non limita i nuovi domini; serve per migrazione installazioni legacy.
- SpamAssassin/Amavis: policy globali; domini Amavis seguono `virtual_alias_domains` generato.
- Submission/relay porta 587 adatta come smart relay per client tipo MDaemon.
- Backup consigliato: volume `mail-data` (DB + chiavi + config generata).
- Per produzione: password forti, MFA admin, `JWT_SECRET` lungo e casuale, rate-limit/firewall sulle porte SMTP.

---

## Licenza

Progetto open source — vedere repository GitHub per dettagli.
