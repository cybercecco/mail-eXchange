import { useEffect, useState } from "react";
import { FormField } from "./FormField";

function destinationLabel(dest) {
  const base = `${dest.host}:${dest.port}`;
  return dest.label && dest.label !== dest.host ? `${dest.label} — ${base}` : base;
}

function parseEmail(email) {
  const at = email.indexOf("@");
  if (at < 0) return { local_part: email, domainName: "" };
  return { local_part: email.slice(0, at), domainName: email.slice(at + 1) };
}

export default function EditMailboxModal({
  open,
  mailbox,
  domains,
  enabledDomains,
  onClose,
  onSave,
  saving
}) {
  const [localPart, setLocalPart] = useState("");
  const [domainId, setDomainId] = useState("");
  const [destinationId, setDestinationId] = useState("");

  const domainOptions =
    mailbox && !enabledDomains.some((d) => d.id === mailbox.domain_id)
      ? [...enabledDomains, ...domains.filter((d) => d.id === mailbox.domain_id)]
      : enabledDomains;

  const selectedDomain = domainOptions.find((d) => String(d.id) === String(domainId));
  const domainDestinations = selectedDomain?.destinations || [];

  useEffect(() => {
    if (!open || !mailbox) return;
    const parsed = parseEmail(mailbox.email);
    const match =
      domains.find((d) => d.name.toLowerCase() === parsed.domainName?.toLowerCase()) ||
      domains.find((d) => d.id === mailbox.domain_id);
    setLocalPart(parsed.local_part);
    setDomainId(String(match?.id ?? enabledDomains[0]?.id ?? ""));
    const dests = match?.destinations || [];
    const destMatch = dests.find(
      (d) =>
        d.host === mailbox.destination_host &&
        Number(d.port) === Number(mailbox.destination_port)
    );
    setDestinationId(destMatch ? String(destMatch.id) : dests[0] ? String(dests[0].id) : "");
  }, [open, mailbox, domains, enabledDomains]);

  useEffect(() => {
    if (!open || !selectedDomain) return;
    const dests = selectedDomain.destinations || [];
    const match = dests.find((d) => String(d.id) === String(destinationId));
    if (!match && dests.length > 0) {
      setDestinationId(String(dests[0].id));
    } else if (!match) {
      setDestinationId("");
    }
  }, [open, selectedDomain?.id, selectedDomain?.destinations?.length, destinationId]);

  useEffect(() => {
    if (!open) return;
    function onKey(e) {
      if (e.key === "Escape" && !saving) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, saving, onClose]);

  if (!open || !mailbox) return null;

  const selectedDestination = domainDestinations.find(
    (d) => String(d.id) === String(destinationId)
  );

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget && !saving) onClose();
      }}
    >
      <div
        className="modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-mailbox-title"
      >
        <div className="modal-header">
          <h3 id="edit-mailbox-title">Modifica casella</h3>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            disabled={saving}
            aria-label="Chiudi"
          >
            ×
          </button>
        </div>

        <p className="panel-hint">
          Modifica indirizzo e server di destinazione. I server disponibili sono quelli configurati
          in Domini per il dominio selezionato.
        </p>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!selectedDomain || !selectedDestination) return;
            const email = `${localPart.trim()}@${selectedDomain.name}`.toLowerCase();
            const patch = {
              destination_host: selectedDestination.host,
              destination_port: Number(selectedDestination.port)
            };
            if (email !== mailbox.email.toLowerCase()) {
              patch.email = email;
            }
            onSave(mailbox.id, patch);
          }}
          className="form-grid"
        >
          <FormField label="Indirizzo email" hint="Parte locale e dominio">
            <div className="form-row">
              <input
                placeholder="utente"
                aria-label="Parte locale email"
                value={localPart}
                onChange={(e) => setLocalPart(e.target.value)}
                required
                disabled={saving}
              />
              <span className="at-sign">@</span>
              <select
                aria-label="Dominio email"
                value={domainId}
                onChange={(e) => {
                  const next = domainOptions.find((d) => String(d.id) === e.target.value);
                  const firstDest = next?.destinations?.[0];
                  setDomainId(e.target.value);
                  setDestinationId(firstDest ? String(firstDest.id) : "");
                }}
                required
                disabled={saving || domainOptions.length === 0}
              >
                {domainOptions.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                    {!d.enabled ? " (disabilitato)" : ""}
                  </option>
                ))}
              </select>
            </div>
          </FormField>
          <FormField
            label="Server di destinazione"
            hint={
              domainDestinations.length === 0
                ? "Configura almeno un server in Domini"
                : undefined
            }
          >
            <select
              value={destinationId}
              onChange={(e) => setDestinationId(e.target.value)}
              required
              disabled={saving || domainDestinations.length === 0}
            >
              {domainDestinations.length === 0 ? (
                <option value="">Nessun server disponibile</option>
              ) : (
                domainDestinations.map((dest) => (
                  <option key={dest.id} value={dest.id}>
                    {destinationLabel(dest)}
                  </option>
                ))
              )}
            </select>
          </FormField>
          <div className="modal-actions">
            <button type="button" className="btn-secondary" onClick={onClose} disabled={saving}>
              Annulla
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={
                saving ||
                !selectedDomain ||
                !localPart.trim() ||
                !selectedDestination
              }
            >
              {saving ? "Salvataggio..." : "Salva modifiche"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
