function imapTargetForDestination(dest) {
  const host = dest.imap_auth_host || dest.host;
  const port = dest.imap_auth_port || 993;
  return { host, port };
}

function domainImapTarget(domain) {
  const dest = domain.destinations?.[0];
  if (!dest) return null;
  return imapTargetForDestination(dest);
}

export default function MobileRelayPage({ domains, settings, settingsLoading }) {
  const smtpHost = settings?.postfix_hostname || "—";
  const smtpPort = settings?.smtp_submission_port || 587;
  const relayDomains = domains.filter((d) => d.enabled && (d.destinations?.length || 0) > 0);

  return (
    <>
      <header className="page-header">
        <h2>Relay mobile (Android / client SMTP)</h2>
        <p>
          L&apos;invio autenticato sulla porta <strong>{smtpPort} (submission)</strong> verifica le
          credenziali con <strong>IMAP LOGIN</strong> sul server MDaemon del dominio. Non servono
          utenti relay locali: usa l&apos;indirizzo casella e la password IMAP del backend.
        </p>
        <p>
          I relay da IP fidati (<code>mynetworks</code> / IP sorgenti per dominio) restano
          disponibili <strong>senza autenticazione</strong>.
        </p>
      </header>

      <div className="panel">
        <h3>Parametri comuni</h3>
        <dl className="kv-list">
          <div>
            <dt>Server SMTP (invio)</dt>
            <dd>
              <code>{smtpHost}</code> — porta <strong>{smtpPort}</strong>, STARTTLS obbligatorio,
              autenticazione attiva
            </dd>
          </div>
          <div>
            <dt>Utente SMTP</dt>
            <dd>
              Indirizzo email completo, es. <code>nome@dominio.it</code>
            </dd>
          </div>
          <div>
            <dt>Password SMTP</dt>
            <dd>Password IMAP dell&apos;utente su MDaemon (stessa della casella)</dd>
          </div>
        </dl>
      </div>

      <div className="panel">
        <h3>Server IMAP per dominio</h3>
        {settingsLoading ? (
          <p className="empty-state">Caricamento impostazioni...</p>
        ) : relayDomains.length === 0 ? (
          <p className="empty-state">
            Nessun dominio abilitato con server destinazione. Configura almeno una destinazione nel
            tab Domini.
          </p>
        ) : (
          <ul className="list-items">
            {relayDomains.map((domain) => {
              const imap = domainImapTarget(domain);
              return (
                <li key={domain.id} className="list-item list-item-stack">
                  <div className="list-item-meta">
                    <strong>{domain.name}</strong>
                    {domain.relay_all_inbound ? (
                      <span> · catch-all relay</span>
                    ) : null}
                  </div>
                  {imap ? (
                    <div className="list-item-meta">
                      <span>
                        IMAP: <code>{imap.host}</code> — porta <strong>{imap.port}</strong>
                        {imap.port === 993 ? " (SSL/TLS)" : " (STARTTLS)"}
                      </span>
                    </div>
                  ) : (
                    <span className="empty-state">Nessuna destinazione configurata</span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        <p className="form-hint" style={{ marginTop: "1rem" }}>
          L&apos;host IMAP predefinito coincide con il server destinazione SMTP. Per override (host o
          porta IMAP diversi) modifica i campi &laquo;IMAP auth&raquo; nella scheda Destinazioni del
          dominio.
        </p>
      </div>

      <div className="panel">
        <h3>Esempio configurazione Android</h3>
        <ol className="numbered-steps">
          <li>
            <strong>Account IMAP</strong> — server = host MDaemon del dominio (tabella sopra), porta
            993 SSL o 143 STARTTLS, email e password utente MDaemon.
          </li>
          <li>
            <strong>Server SMTP in uscita</strong> — <code>{smtpHost}</code>, porta{" "}
            <strong>{smtpPort}</strong>, sicurezza STARTTLS, stesse credenziali (email + password
            IMAP).
          </li>
          <li>
            Verifica che il dominio sia abilitato e abbia un server destinazione in Mail Exchange.
          </li>
        </ol>
      </div>
    </>
  );
}

export { imapTargetForDestination, domainImapTarget };
