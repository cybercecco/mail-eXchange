import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import DomainDnsPanel from "./DomainDnsPanel";

export default function DomainDnsSettingsPanel({ domainName, active }) {
  const [check, setCheck] = useState(null);
  const [loading, setLoading] = useState(false);

  const loadDns = useCallback(async () => {
    const domain = domainName?.trim();
    if (!domain) {
      setCheck(null);
      return;
    }
    setLoading(true);
    try {
      const data = await api(`/dns/check?domain=${encodeURIComponent(domain)}`);
      setCheck(data);
    } catch (err) {
      if (!err.unauthorized) {
        alert(err.message);
      }
    } finally {
      setLoading(false);
    }
  }, [domainName]);

  useEffect(() => {
    if (!active || !domainName?.trim()) return;
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        const data = await api(`/dns/check?domain=${encodeURIComponent(domainName.trim())}`);
        if (!cancelled) setCheck(data);
      } catch (err) {
        if (!cancelled && !err.unauthorized) {
          alert(err.message);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [active, domainName]);

  if (!domainName) {
    return <p className="empty-state">Dominio non disponibile.</p>;
  }

  return (
    <div className="domain-settings-panel domain-dns">
      <div className="panel-actions dns-toolbar">
        <div className="dns-toolbar-fields">
          <h4 className="domain-destinations__title">Controllo record DNS</h4>
          <p className="panel-hint" style={{ margin: 0 }}>
            Verifica SPF, DKIM e DMARC per <strong>{domainName}</strong>.
          </p>
        </div>
        <button type="button" className="btn-secondary" onClick={loadDns} disabled={loading}>
          {loading ? "Verifica..." : "Aggiorna DNS"}
        </button>
      </div>

      {loading && !check ? (
        <p className="empty-state">Caricamento controllo DNS...</p>
      ) : check?.domain?.toLowerCase() === domainName.trim().toLowerCase() ? (
        <>
          <p style={{ margin: "0 0 1rem", fontSize: "0.9rem", color: "var(--text-muted)" }}>
            Host SMTP: <strong>{check.hostname}</strong>
            {check.smtp_hostname ? (
              <>
                {" "}
                · host verificato SPF: <code>{check.smtp_hostname}</code>
              </>
            ) : null}
          </p>
          <DomainDnsPanel check={check} />
        </>
      ) : (
        <p className="empty-state">Premi «Aggiorna DNS» per avviare la verifica.</p>
      )}
    </div>
  );
}
