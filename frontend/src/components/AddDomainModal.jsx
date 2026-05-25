import { useEffect } from "react";
import { FormField } from "./FormField";

export default function AddDomainModal({
  open,
  onClose,
  domainForm,
  setDomainForm,
  busy,
  onSubmit
}) {
  useEffect(() => {
    if (!open) return;
    function onKey(e) {
      if (e.key === "Escape" && !busy) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  if (!open) return null;

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div className="modal-dialog" role="dialog" aria-modal="true" aria-labelledby="add-domain-title">
        <div className="modal-header">
          <h3 id="add-domain-title">Aggiungi dominio</h3>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            disabled={busy}
            aria-label="Chiudi"
          >
            ×
          </button>
        </div>

        <form onSubmit={onSubmit} className="form-grid">
          <FormField label="Nome dominio" htmlFor="domain-name">
            <input
              id="domain-name"
              placeholder="esempio.com"
              value={domainForm.name}
              onChange={(e) => setDomainForm({ ...domainForm, name: e.target.value })}
              required
              disabled={busy}
              autoFocus
            />
          </FormField>
          <FormField label="Selector DKIM" htmlFor="domain-dkim" hint="Default: mail" hintAfter>
            <input
              id="domain-dkim"
              placeholder="mail"
              value={domainForm.dkim_selector}
              onChange={(e) => setDomainForm({ ...domainForm, dkim_selector: e.target.value })}
              disabled={busy}
            />
          </FormField>
          <FormField
            label="Server Cluster (FQDN)"
            htmlFor="domain-sibling"
            hint="Opzionale: replica automatica verso un altro nodo (configura anche la chiave precondivisa nel tab Cluster)."
            hintAfter
          >
            <input
              id="domain-sibling"
              placeholder="mx2.example.com"
              value={domainForm.sibling_fqdn}
              onChange={(e) => setDomainForm({ ...domainForm, sibling_fqdn: e.target.value })}
              disabled={busy}
            />
          </FormField>
          <div className="modal-actions">
            <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>
              Annulla
            </button>
            <button type="submit" className="btn-primary" disabled={busy}>
              {busy ? "Aggiunta..." : "Aggiungi dominio"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
