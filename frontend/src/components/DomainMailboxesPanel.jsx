import { useEffect, useState } from "react";
import EditMailboxModal from "./EditMailboxModal";
import ImportCsvModal from "./ImportCsvModal";
import { FormField } from "./FormField";

function destinationLabel(dest) {
  const base = `${dest.host}:${dest.port}`;
  return dest.label && dest.label !== dest.host ? `${dest.label} — ${base}` : base;
}

export default function DomainMailboxesPanel({
  domain,
  domains,
  enabledDomains,
  mailboxes,
  mailboxForm,
  setMailboxForm,
  onAddMailbox,
  onUpdateMailbox,
  onDeleteMailbox,
  onImportCsv,
  importUpdateExisting,
  setImportUpdateExisting,
  importSkipHeader,
  setImportSkipHeader,
  importResult,
  importBusy
}) {
  const [importOpen, setImportOpen] = useState(false);
  const [editingMailbox, setEditingMailbox] = useState(null);
  const [editSaving, setEditSaving] = useState(false);
  const [filterSearch, setFilterSearch] = useState("");

  const domainDestinations = domain?.destinations || [];
  const domainEnabled = !!domain?.enabled;

  useEffect(() => {
    if (!domain) return;
    const dests = domain.destinations || [];
    setMailboxForm((prev) => {
      if (String(prev.domain_id) !== String(domain.id)) {
        return {
          ...prev,
          domain_id: String(domain.id),
          destination_id: dests[0] ? String(dests[0].id) : ""
        };
      }
      const match = dests.find((d) => String(d.id) === String(prev.destination_id));
      if (match) return prev;
      if (dests.length > 0) {
        return { ...prev, destination_id: String(dests[0].id) };
      }
      return { ...prev, destination_id: "" };
    });
  }, [domain?.id, domain?.destinations?.length, setMailboxForm]);

  const searchQuery = filterSearch.trim().toLowerCase();
  const domainMailboxes = mailboxes.filter(
    (item) => String(item.domain_id) === String(domain.id)
  );
  const filteredMailboxes = searchQuery
    ? domainMailboxes.filter(
        (item) =>
          item.email?.toLowerCase().includes(searchQuery) ||
          item.destination_host?.toLowerCase().includes(searchQuery)
      )
    : domainMailboxes;

  return (
    <div className="domain-settings-panel domain-mailboxes">
      <div className="domain-mailboxes__header">
        <div>
          <h4 className="domain-destinations__title">Caselle di {domain.name}</h4>
          <p className="panel-hint">
            Indirizzi e routing SMTP per questo dominio. I server di destinazione si configurano
            nel sotto-tab Destinazioni.
          </p>
        </div>
        <button
          type="button"
          className="btn-secondary btn-sm"
          onClick={() => setImportOpen(true)}
          disabled={!domainEnabled || enabledDomains.length === 0}
        >
          Importa CSV
        </button>
      </div>

      <EditMailboxModal
        open={editingMailbox !== null}
        mailbox={editingMailbox}
        domains={domains}
        enabledDomains={enabledDomains}
        saving={editSaving}
        onClose={() => {
          if (!editSaving) setEditingMailbox(null);
        }}
        onSave={async (id, patch) => {
          setEditSaving(true);
          try {
            await onUpdateMailbox(id, patch);
            setEditingMailbox(null);
          } finally {
            setEditSaving(false);
          }
        }}
      />

      <ImportCsvModal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        enabledDomains={enabledDomains}
        importUpdateExisting={importUpdateExisting}
        setImportUpdateExisting={setImportUpdateExisting}
        importSkipHeader={importSkipHeader}
        setImportSkipHeader={setImportSkipHeader}
        importResult={importResult}
        importBusy={importBusy}
        onImportCsv={onImportCsv}
      />

      <form
        onSubmit={onAddMailbox}
        className="form-grid form-grid--inline form-grid--inline-mailbox domain-mailboxes__add-form"
      >
        <FormField label="Indirizzo email" hint="Parte locale dell'indirizzo" hintAfter>
          <div className="form-row">
            <input
              id={`mailbox-local-${domain.id}`}
              placeholder="utente"
              aria-label="Parte locale email"
              value={mailboxForm.local_part}
              onChange={(e) => setMailboxForm({ ...mailboxForm, local_part: e.target.value })}
              required
              disabled={!domainEnabled}
            />
            <span className="at-sign">@{domain.name}</span>
          </div>
        </FormField>
        <FormField
          label="Server di destinazione"
          htmlFor={`mailbox-dest-${domain.id}`}
          hint={
            domainDestinations.length === 0
              ? "Aggiungi server nel sotto-tab Destinazioni"
              : "Scegli tra i server configurati per questo dominio"
          }
          hintAfter
        >
          <select
            id={`mailbox-dest-${domain.id}`}
            value={mailboxForm.destination_id}
            onChange={(e) =>
              setMailboxForm({ ...mailboxForm, destination_id: e.target.value })
            }
            required
            disabled={!domainEnabled || domainDestinations.length === 0}
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
        <div className="form-actions">
          <button
            type="submit"
            className="btn-primary btn-sm"
            disabled={
              !domainEnabled || domainDestinations.length === 0
            }
          >
            Aggiungi casella
          </button>
        </div>
      </form>

      {!domainEnabled ? (
        <p className="panel-hint panel-hint--warn" role="alert">
          Il dominio è disabilitato: abilitalo nel sotto-tab Generale per aggiungere caselle.
        </p>
      ) : null}

      <div className="domain-mailboxes__list">
        <div className="panel-actions mailboxes-toolbar domain-mailboxes__toolbar">
          <div className="dns-toolbar-fields">
            <h4 className="panel-actions__title domain-mailboxes__list-title">Elenco caselle</h4>
            <div className="mailboxes-toolbar-filters">
              <FormField label="Cerca" htmlFor={`mailbox-search-${domain.id}`} className="form-field--row">
                <input
                  id={`mailbox-search-${domain.id}`}
                  type="search"
                  placeholder="Email o host destinazione"
                  value={filterSearch}
                  onChange={(e) => setFilterSearch(e.target.value)}
                />
              </FormField>
            </div>
          </div>
        </div>
        {domainMailboxes.length === 0 ? (
          <p className="empty-state">Nessuna casella configurata per questo dominio.</p>
        ) : filteredMailboxes.length === 0 ? (
          <p className="empty-state">Nessuna casella corrisponde alla ricerca.</p>
        ) : (
          <ul className="list-items list-items--compact">
            {filteredMailboxes.map((item) => (
              <li key={item.id} className="list-item">
                <div className="list-item-meta">
                  <strong>{item.email}</strong>
                  <span>
                    → {item.destination_host}:{item.destination_port}
                  </span>
                </div>
                <div className="list-item-actions">
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => setEditingMailbox(item)}
                  >
                    Modifica
                  </button>
                  <button
                    type="button"
                    className="btn-danger btn-sm"
                    onClick={() => onDeleteMailbox(item.id)}
                  >
                    Elimina
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
