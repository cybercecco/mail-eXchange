import DomainDnsPanel from "../components/DomainDnsPanel";
import { FormField } from "../components/FormField";

export default function DnsPage({
  dns,
  dnsLoading,
  enabledDomains,
  dnsDomain,
  onDnsDomainChange,
  onRefreshDns
}) {
  const hasEnabled = enabledDomains.length > 0;
  const check = dns?.domain ? dns : null;

  return (
    <>
      <header className="page-header">
        <h2>DNS (SPF, DKIM, DMARC)</h2>
        <p>Verifica i record di autenticazione e deliverability per il dominio selezionato.</p>
      </header>

      <div className="panel">
        <div className="panel-actions dns-toolbar">
          <div className="dns-toolbar-fields">
            <h3 className="panel-actions__title">Controllo record</h3>
            {hasEnabled ? (
              <FormField label="Dominio" htmlFor="dns-domain" className="form-field--row">
                <select
                  id="dns-domain"
                  value={dnsDomain}
                  onChange={(e) => onDnsDomainChange(e.target.value)}
                  disabled={dnsLoading}
                >
                  {enabledDomains.map((d) => (
                    <option key={d.id} value={d.name}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </FormField>
            ) : null}
          </div>
          <button
            type="button"
            className="btn-secondary"
            onClick={onRefreshDns}
            disabled={dnsLoading || !hasEnabled || !dnsDomain}
          >
            {dnsLoading ? "Verifica..." : "Aggiorna DNS"}
          </button>
        </div>

        {!hasEnabled ? (
          <p className="empty-state">
            Nessun dominio abilitato. Abilita un dominio dalla pagina Domini per verificare i record DNS
            (in assenza di domini il server usa MAIL_DOMAIN).
          </p>
        ) : dnsLoading && !dns ? (
          <p className="empty-state">Caricamento controllo DNS...</p>
        ) : check ? (
          <>
            <p style={{ margin: "0 0 1rem", fontSize: "0.9rem", color: "var(--text-muted)" }}>
              Host SMTP: <strong>{check.hostname || dns?.hostname}</strong>
            </p>
            <DomainDnsPanel check={check} />
          </>
        ) : (
          <p className="empty-state">Seleziona un dominio e premi «Aggiorna DNS» per avviare la verifica.</p>
        )}
      </div>
    </>
  );
}
