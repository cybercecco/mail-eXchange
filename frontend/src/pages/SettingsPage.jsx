import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import DnsRecordCard from "../components/DnsRecordCard";
import { FormField } from "../components/FormField";
import StatusBadge from "../components/StatusBadge";

const BYTES_PER_MB = 1024 * 1024;

function bytesToMb(bytes) {
  return Math.round((bytes / BYTES_PER_MB) * 100) / 100;
}

function mbToBytes(mb) {
  return Math.round(Number(mb) * BYTES_PER_MB);
}

export default function SettingsPage({ onError, isAdmin }) {
  const [form, setForm] = useState({
    public_url: "",
    acme_email: "",
    docker_dns_servers: ""
  });
  const [caddyPorts, setCaddyPorts] = useState({ http: 60080, https: 60443 });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveResult, setSaveResult] = useState(null);

  const [domains, setDomains] = useState([]);
  const [mailboxes, setMailboxes] = useState([]);
  const [testDomainId, setTestDomainId] = useState("");
  const [testMailboxId, setTestMailboxId] = useState("");
  const [testSending, setTestSending] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [testError, setTestError] = useState("");
  const [errorPreview, setErrorPreview] = useState(null);
  const [errorNotifyBusy, setErrorNotifyBusy] = useState(false);
  const [errorNotifyResult, setErrorNotifyResult] = useState("");

  const [postfixForm, setPostfixForm] = useState({
    message_size_mb: 10,
    mailbox_size_mb: 50,
    smtpd_timeout: 300
  });
  const [postfixLoading, setPostfixLoading] = useState(true);
  const [postfixSaving, setPostfixSaving] = useState(false);
  const [postfixSaved, setPostfixSaved] = useState(false);

  const enabledDomains = useMemo(() => domains.filter((d) => d.enabled), [domains]);

  const mailboxesForDomain = useMemo(() => {
    if (!testDomainId) return [];
    return mailboxes.filter(
      (m) => String(m.domain_id) === String(testDomainId) && m.enabled
    );
  }, [mailboxes, testDomainId]);

  const loadMailData = useCallback(async () => {
    const [d, m] = await Promise.all([api("/domains"), api("/mailboxes")]);
    setDomains(d);
    setMailboxes(m);
    const firstDomain = d.find((x) => x.enabled) || d[0];
    if (firstDomain) {
      setTestDomainId(String(firstDomain.id));
      const firstMb = m.find(
        (x) => x.domain_id === firstDomain.id && x.enabled
      );
      setTestMailboxId(firstMb ? String(firstMb.id) : "");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      onError?.("");
      try {
        const data = await api("/settings");
        if (!cancelled) {
          setForm({
            public_url: data.public_url || "",
            acme_email: data.acme_email || "",
            docker_dns_servers:
              data.docker_dns_servers ||
              (Array.isArray(data.docker_dns) ? data.docker_dns.join("\n") : "")
          });
          setCaddyPorts({
            http: data.caddy_http_port ?? 60080,
            https: data.caddy_https_port ?? 60443
          });
        }
        if (!cancelled) await loadMailData();
      } catch (err) {
        if (!cancelled) onError?.(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [onError, loadMailData]);

  useEffect(() => {
    let cancelled = false;
    async function loadPostfix() {
      setPostfixLoading(true);
      try {
        const data = await api("/postfix");
        if (!cancelled) {
          setPostfixForm({
            message_size_mb: bytesToMb(data.message_size_limit),
            mailbox_size_mb: bytesToMb(data.mailbox_size_limit),
            smtpd_timeout: data.smtpd_timeout
          });
        }
      } catch (err) {
        if (!cancelled) onError?.(err.message);
      } finally {
        if (!cancelled) setPostfixLoading(false);
      }
    }
    loadPostfix();
    return () => {
      cancelled = true;
    };
  }, [onError]);

  useEffect(() => {
    const match = mailboxesForDomain.find((m) => String(m.id) === String(testMailboxId));
    if (!match && mailboxesForDomain.length > 0) {
      setTestMailboxId(String(mailboxesForDomain[0].id));
    } else if (!match) {
      setTestMailboxId("");
    }
  }, [testDomainId, mailboxesForDomain, testMailboxId]);

  async function handleSubmit(event) {
    event.preventDefault();
    setSaving(true);
    setSaveResult(null);
    onError?.("");
    try {
      const body = {
        public_url: form.public_url.trim(),
        acme_email: form.acme_email.trim(),
        docker_dns_servers: form.docker_dns_servers.trim()
      };
      const result = await api("/settings", {
        method: "PUT",
        body: JSON.stringify(body)
      });
      setSaveResult(result);
    } catch (err) {
      onError?.(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handlePostfixSubmit(event) {
    event.preventDefault();
    setPostfixSaving(true);
    setPostfixSaved(false);
    onError?.("");
    try {
      const body = {
        message_size_limit: mbToBytes(postfixForm.message_size_mb),
        mailbox_size_limit: mbToBytes(postfixForm.mailbox_size_mb),
        smtpd_timeout: Number(postfixForm.smtpd_timeout)
      };
      await api("/postfix", {
        method: "PUT",
        body: JSON.stringify(body)
      });
      setPostfixSaved(true);
    } catch (err) {
      onError?.(err.message);
    } finally {
      setPostfixSaving(false);
    }
  }

  async function handleTestMail(event) {
    event.preventDefault();
    if (!testDomainId || !testMailboxId) return;
    setTestSending(true);
    setTestResult(null);
    setTestError("");
    try {
      const result = await api("/settings/test-mail", {
        method: "POST",
        body: JSON.stringify({
          domain_id: Number(testDomainId),
          mailbox_id: Number(testMailboxId)
        })
      });
      setTestResult(result);
      if (result.smtp_error) {
        setTestError(result.smtp_error);
      }
    } catch (err) {
      const message = err?.message ? String(err.message) : String(err);
      setTestError(message);
    } finally {
      setTestSending(false);
    }
  }

  const selectedMailbox = mailboxesForDomain.find(
    (m) => String(m.id) === String(testMailboxId)
  );

  const errorPreviewBySource = useMemo(() => {
    if (!errorPreview?.entries?.length) return [];
    const groups = new Map();
    for (const entry of errorPreview.entries) {
      const source = entry.source || "Altro";
      if (!groups.has(source)) groups.set(source, []);
      groups.get(source).push(entry.line);
    }
    return Array.from(groups.entries());
  }, [errorPreview]);

  return (
    <>
      <header className="page-header">
        <h2>Configurazione</h2>
        <p>
          URL pubblico per HTTPS/Let&apos;s Encrypt, controlli DNS e test di invio attraverso Postfix.
          Caddy espone HTTP sulla porta {caddyPorts.http} e HTTPS sulla {caddyPorts.https}; se serve
          l&apos;accesso standard (80/443), configura NAT o firewall esterno (es. 443→{caddyPorts.https}).
          Il token Cloudflare per certificati Let&apos;s Encrypt (DNS-01) va in <code>CLOUDFLARE_API_TOKEN</code> nel file <code>.env</code>.
        </p>
      </header>

      <div className="panel">
        <h3>Impostazioni pubbliche</h3>
        {loading ? (
          <p className="empty-state">Caricamento...</p>
        ) : (
          <form onSubmit={handleSubmit} className="form-grid">
            <FormField
              label="URL pubblico del server"
              htmlFor="settings-public-url"
              hint="Hostname FQDN, es. smtp.example.com (senza https://)"
            >
              <input
                id="settings-public-url"
                placeholder="smtp.example.com"
                value={form.public_url}
                onChange={(e) => setForm({ ...form, public_url: e.target.value })}
                required
                disabled={saving}
              />
            </FormField>
            <FormField
              label="Email ACME (Let's Encrypt)"
              htmlFor="settings-acme-email"
              hint="Indirizzo per registrazione certificato"
            >
              <input
                id="settings-acme-email"
                type="email"
                placeholder="admin@example.com"
                value={form.acme_email}
                onChange={(e) => setForm({ ...form, acme_email: e.target.value })}
                required
                disabled={saving}
              />
            </FormField>
            <FormField
              label="DNS per container Docker"
              htmlFor="settings-docker-dns"
              hint="Un indirizzo per riga (o separati da virgola). Usati da Postfix, Amavis, API e controlli SPF/DKIM. Max 4."
            >
              <textarea
                id="settings-docker-dns"
                rows={3}
                placeholder={"208.67.222.222\n208.67.220.220"}
                value={form.docker_dns_servers}
                onChange={(e) => setForm({ ...form, docker_dns_servers: e.target.value })}
                disabled={saving}
              />
            </FormField>
            <div className="form-actions">
              <button type="submit" className="btn-primary" disabled={saving}>
                {saving ? "Salvataggio..." : "Salva impostazioni"}
              </button>
              {saveResult && (
                <span
                  className={saveResult.dns_applied ? "mfa-success" : "panel-hint"}
                  role={saveResult.dns_applied ? undefined : "alert"}
                >
                  {saveResult.dns_apply_message ||
                    (saveResult.dns_applied
                      ? "Impostazioni salvate. DNS applicati automaticamente ai container."
                      : "Impostazioni salvate. Applicazione DNS non completata.")}
                </span>
              )}
            </div>
          </form>
        )}
      </div>

      <div className="panel">
        <h3>Parametri Postfix</h3>
        <p className="panel-hint">
          Limiti generali del server SMTP in transito. Dopo il salvataggio i valori vengono
          applicati automaticamente a Postfix (ricarica configurazione).
        </p>
        {postfixLoading ? (
          <p className="empty-state">Caricamento...</p>
        ) : (
          <form onSubmit={handlePostfixSubmit} className="form-grid">
            <FormField
              label="Dimensione massima messaggio (MB)"
              htmlFor="postfix-message-size"
              hint="message_size_limit — default Postfix 10 MB"
            >
              <input
                id="postfix-message-size"
                type="number"
                min={1}
                max={100}
                step={0.1}
                value={postfixForm.message_size_mb}
                onChange={(e) =>
                  setPostfixForm({ ...postfixForm, message_size_mb: e.target.value })
                }
                required
                disabled={postfixSaving}
              />
            </FormField>
            <FormField
              label="Dimensione massima casella (MB)"
              htmlFor="postfix-mailbox-size"
              hint="mailbox_size_limit — deve essere ≥ dimensione messaggio"
            >
              <input
                id="postfix-mailbox-size"
                type="number"
                min={1}
                max={500}
                step={0.1}
                value={postfixForm.mailbox_size_mb}
                onChange={(e) =>
                  setPostfixForm({ ...postfixForm, mailbox_size_mb: e.target.value })
                }
                required
                disabled={postfixSaving}
              />
            </FormField>
            <FormField
              label="Timeout client SMTP (secondi)"
              htmlFor="postfix-smtpd-timeout"
              hint="smtpd_timeout — tempo massimo di inattività del client"
            >
              <input
                id="postfix-smtpd-timeout"
                type="number"
                min={30}
                max={3600}
                step={1}
                value={postfixForm.smtpd_timeout}
                onChange={(e) =>
                  setPostfixForm({ ...postfixForm, smtpd_timeout: e.target.value })
                }
                required
                disabled={postfixSaving}
              />
            </FormField>
            <div className="form-actions">
              <button type="submit" className="btn-primary" disabled={postfixSaving}>
                {postfixSaving ? "Salvataggio..." : "Salva parametri Postfix"}
              </button>
              {postfixSaved && <span className="mfa-success">Parametri Postfix salvati.</span>}
            </div>
          </form>
        )}
      </div>

      <div className="panel">
        <h3>Test invio mail</h3>
        <p className="panel-hint">
          Invia un messaggio di prova a una casella configurata e analizza in parallelo SPF, DKIM e DMARC del
          dominio (DNS Umbrella). Il messaggio transita Postfix, antispam/antivirus e il routing configurato.
        </p>
        {loading ? (
          <p className="empty-state">Caricamento...</p>
        ) : enabledDomains.length === 0 ? (
          <p className="empty-state">Aggiungi almeno un dominio abilitato in Domini.</p>
        ) : (
          <form onSubmit={handleTestMail} className="form-grid">
            <FormField label="Dominio" htmlFor="test-mail-domain">
              <select
                id="test-mail-domain"
                value={testDomainId}
                onChange={(e) => setTestDomainId(e.target.value)}
                disabled={testSending}
              >
                {enabledDomains.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField
              label="Destinatario (casella)"
              htmlFor="test-mail-mailbox"
              hint={
                mailboxesForDomain.length === 0
                  ? "Nessuna casella abilitata per questo dominio"
                  : selectedMailbox
                    ? `→ ${selectedMailbox.destination_host}:${selectedMailbox.destination_port}`
                    : undefined
              }
              hintAfter
            >
              <select
                id="test-mail-mailbox"
                value={testMailboxId}
                onChange={(e) => setTestMailboxId(e.target.value)}
                disabled={testSending || mailboxesForDomain.length === 0}
                required
              >
                {mailboxesForDomain.length === 0 ? (
                  <option value="">Nessuna casella</option>
                ) : (
                  mailboxesForDomain.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.email}
                    </option>
                  ))
                )}
              </select>
            </FormField>
            <div className="form-actions">
              <button
                type="submit"
                className="btn-secondary"
                disabled={testSending || !testMailboxId}
              >
                {testSending ? "Invio..." : "Invia mail di test"}
              </button>
            </div>
          </form>
        )}
        {testResult?.smtp && (
          <p className="health-ok" style={{ marginTop: "0.75rem" }}>
            <span className="health-dot" />
            Messaggio accodato: da <code>{testResult.smtp.from}</code> a <code>{testResult.smtp.to}</code>
            {testResult.smtp.destination ? (
              <>
                {" "}
                (routing <code>{testResult.smtp.destination}</code>)
              </>
            ) : null}
          </p>
        )}
        {testResult?.dns_check && (
          <div className="test-mail-dns" style={{ marginTop: "1rem" }}>
            <div className="test-mail-dns__header">
              <h4>Analisi autenticazione DNS</h4>
              <StatusBadge status={testResult.dns_overall || "ok"} />
            </div>
            {testResult.dns_check.smtp_hostname && (
              <p className="panel-hint">
                Host SMTP verificato per SPF: <code>{testResult.dns_check.smtp_hostname}</code>
                {testResult.dns_check.dkim_selector ? (
                  <>
                    {" "}
                    · selector DKIM: <code>{testResult.dns_check.dkim_selector}</code>
                  </>
                ) : null}
              </p>
            )}
            <div className="dns-domain-block">
              <DnsRecordCard title="SPF" record={testResult.dns_check.spf} />
              <DnsRecordCard
                title="DKIM"
                record={testResult.dns_check.dkim}
                expectedLabel="Record TXT da configurare:"
                expectedAsPre
              />
              <DnsRecordCard title="DMARC" record={testResult.dns_check.dmarc} />
            </div>
          </div>
        )}
        {testError && (
          <div className="test-mail-error" role="alert">
            <strong>Errore invio test</strong>
            <pre>{testError}</pre>
          </div>
        )}
      </div>

      {isAdmin && (
        <div className="panel">
          <h3>Notifiche errori stack</h3>
          <p className="panel-hint">
            Gli utenti con email impostata in Il mio account o in Utenti ricevono automaticamente
            un report solo in caso di guasti (errori/malfunzionamenti, non warning) rilevati negli
            ultimi 30 minuti nei log Postfix/Amavis. Puoi forzare l&apos;invio da qui.
          </p>
          <div className="form-actions">
            <button
              type="button"
              className="btn-secondary"
              disabled={errorNotifyBusy}
              onClick={async () => {
                setErrorNotifyBusy(true);
                setErrorNotifyResult("");
                onError?.("");
                try {
                  const preview = await api("/notifications/errors/preview");
                  setErrorPreview(preview);
                } catch (err) {
                  onError?.(err.message);
                } finally {
                  setErrorNotifyBusy(false);
                }
              }}
            >
              Anteprima errori
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={errorNotifyBusy}
              onClick={async () => {
                if (!confirm("Inviare ora il report errori a tutti gli utenti con email configurata?")) {
                  return;
                }
                setErrorNotifyBusy(true);
                setErrorNotifyResult("");
                onError?.("");
                try {
                  const result = await api("/notifications/errors/send?force=true", {
                    method: "POST"
                  });
                  if (result.status === "skipped" && result.reason === "no_errors") {
                    setErrorNotifyResult(
                      result.message ||
                        "Nessun guasto negli ultimi 30 minuti: invio non eseguito."
                    );
                  } else {
                    setErrorNotifyResult(
                      `Invio: ${result.sent}/${result.recipients} destinatari · ${result.error_count} guasti (ultimi ${result.window_minutes ?? 30} min)`
                    );
                  }
                } catch (err) {
                  onError?.(err.message);
                } finally {
                  setErrorNotifyBusy(false);
                }
              }}
            >
              {errorNotifyBusy ? "Invio..." : "Invia report errori ora"}
            </button>
          </div>
          {errorPreview && (
            <div className="error-preview">
              <p className="panel-hint">
                Anteprima: {errorPreview.count}{" "}
                {errorPreview.count === 1
                  ? "guasto"
                  : "guasti"}{" "}
                negli ultimi {errorPreview.window_minutes ?? 30} minuti.
              </p>
              {errorPreview.count === 0 ? (
                <p className="empty-state">
                  Nessun guasto negli ultimi {errorPreview.window_minutes ?? 30} minuti.
                </p>
              ) : (
                <div className="error-preview-log">
                  {errorPreviewBySource.map(([source, lines]) => (
                    <div key={source} className="error-preview-group">
                      <strong>{source}</strong>
                      <pre>{lines.join("\n")}</pre>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          {errorNotifyResult && <p className="mfa-success">{errorNotifyResult}</p>}
        </div>
      )}
    </>
  );
}
