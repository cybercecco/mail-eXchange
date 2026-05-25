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
| **Caddy** | Reverse proxy HTTPS per la UI, certificati Let's Encrypt (HTTP-01 o DNS-01 Cloudflare) |
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
                                   │ POST /api/sync/mailboxes
                                   │ Bearer SYNC_SHARED_SECRET
                    ┌──────────────▼──────────────────────┐
                    │  Nodo B (mx2.example.com)           │
                    │  (stessa stack, DB indipendente)    │
                    └─────────────────────────────────────┘
```

- Ogni nodo ha **database SQLite proprio** e configurazione locale (dominio, destinazioni, impostazioni).
- La sync cluster replica **solo le caselle** di un dominio verso il peer configurato in `sibling_fqdn`.
- Il nodo che modifica i dati è **fonte di verità**; il push sovrascrive le caselle sul peer.
- `sibling_fqdn`, destinazioni e impostazioni di sistema **non** vengono sincronizzate.
- Porta HTTPS predefinita per sync: **60443** (`SYNC_HTTPS_PORT`).

Per produzione multi-nodo tipico:

1. Due (o più) server con stack identica, `.env` con lo stesso `SYNC_SHARED_SECRET`.
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

- Campo **FQDN Server Cluster** (`sibling_fqdn`): peer che riceve push HTTPS delle caselle.
- Sync automatica dopo: salvataggio cluster, creazione/modifica/eliminazione casella, import CSV.
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
- Colonne riconosciute (header): `mail`/`email`, `destinazione`/`destination`/`host`, `porta`/`port` (opzionale, default 25).
- Opzioni import:
  - **Salta prima riga** (intestazione).
  - **Aggiorna caselle esistenti** (stessa email).

Esempio:

```csv
mail,destinazione,porta
user@example.com,192.168.1.10,25
admin@example.com,mail.internal,587
```

Requisiti: dominio abilitato per l'email; destinazione deve esistere nel dominio.

---

## Sincronizzazione cluster / Cluster sync

| Variabile | Descrizione |
|-----------|-------------|
| `SYNC_SHARED_SECRET` | Segreto condiviso tra i nodi (header `Authorization: Bearer`) |
| `SYNC_TLS_VERIFY` | Verifica certificato TLS peer (default `true`) |
| `SYNC_HTTPS_PORT` | Porta HTTPS per push (default `60443`) |
| `PUBLIC_HOSTNAME` | Evita sync verso se stessi |

**Endpoint ricevente:** `POST /api/sync/mailboxes` (autenticazione Bearer).

**Payload:** `{ "domain_name": "...", "mailboxes": [{ "email", "destination_host", "destination_port", "enabled" }] }`.

Il ricevente crea il dominio se assente, upsert/delete caselle, rigenera Postfix. Destinazioni e impostazioni dominio restano locali.

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
| **Token Cloudflare** | DNS-01 ACME (permesso DNS Edit); salvato cifrato nel DB, mai loggato |
| **DNS container** | Resolver Docker per tutti i servizi stack |

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

Rigenerazione `spamassassin.local.cf` e override Amavis.

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

### Deploy da Docker Hub (pull-only, senza build)

```bash
cp .env.example .env   # oppure .env.production sul server
# Impostare DOCKERHUB_NAMESPACE=cybercecco e MAIL_EXCHANGE_IMAGE_TAG=latest

docker compose -f docker-compose.yml -f docker-compose.hub.yml pull
docker compose -f docker-compose.yml -f docker-compose.hub.yml up -d --no-build
```

Porte predefinite:

| Porta host | Servizio |
|------------|----------|
| **60080** | Caddy HTTP (ACME HTTP-01) |
| **60443** | Caddy HTTPS (UI) |
| **25** / **587** | Postfix SMTP / submission (`SMTP_PUBLISHED_PORT`, `SUBMISSION_PUBLISHED_PORT`) |

Sovrascrivibili con `CADDY_HTTP_PORT`, `CADDY_HTTPS_PORT` in `.env`.

### Deploy remoto SSH

```bash
./deploy.sh root@172.22.11.125 /opt/mail-exchange
```

Richiede `.env.production` locale. Copia sorgenti (esclusi segreti), esegue `docker compose pull && up --build` sul remoto, applica override DNS generati.

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
  id, name UNIQUE, enabled, dkim_selector, sibling_fqdn,
  relay_all_inbound, relay_source_ips,
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
| POST | `/api/sync/mailboxes` | Sync cluster (Bearer secret) |

### Sicurezza e DNS

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET/PUT | `/api/spamassassin` | Policy antispam |
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
| `SYNC_SHARED_SECRET` | — | Segreto Bearer inter-server |
| `SYNC_TLS_VERIFY` | `true` | Verifica cert TLS peer |
| `SYNC_HTTPS_PORT` | `60443` | Porta HTTPS sync |

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

docker compose up -d --build
```

1. Apri `http://localhost:60080` o `https://<host>:60443`.
2. Accedi con credenziali admin bootstrap.
3. **Domini** → aggiungi dominio → tab Destinazioni → tab Caselle.
4. (Consigliato) **Il mio account** → MFA + cambio password.
5. **Configurazione** → URL pubblico e token Cloudflare se serve TLS DNS-01.

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
