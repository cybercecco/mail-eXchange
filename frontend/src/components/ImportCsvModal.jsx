import { useEffect } from "react";
import { CheckboxField, FormField } from "./FormField";

export default function ImportCsvModal({
  open,
  onClose,
  enabledDomains,
  importUpdateExisting,
  setImportUpdateExisting,
  importSkipHeader,
  setImportSkipHeader,
  importResult,
  importBusy,
  onImportCsv
}) {
  useEffect(() => {
    if (!open) return;
    function onKey(e) {
      if (e.key === "Escape" && !importBusy) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, importBusy, onClose]);

  if (!open) return null;

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget && !importBusy) onClose();
      }}
    >
      <div className="modal-dialog" role="dialog" aria-modal="true" aria-labelledby="import-csv-title">
        <div className="modal-header">
          <h3 id="import-csv-title">Importa caselle da CSV</h3>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            disabled={importBusy}
            aria-label="Chiudi"
          >
            ×
          </button>
        </div>

        <p className="panel-hint">
          Colonne: <code>mail</code> (oppure <code>local</code> + <code>domain</code>) e{" "}
          <code>destination_label</code> (etichetta del server in Domini, non host/porta).
          Con intestazione nella prima riga, lascia il checkbox deselezionato; senza intestazione l&apos;ordine è
          mail, etichetta. Il dominio si ricava dall&apos;email; l&apos;etichetta deve esistere tra le destinazioni del
          dominio (confronto senza distinzione maiuscole/minuscole). CSV con host/porta è ancora accettato ma deprecato.
        </p>
        <pre className="csv-example">{`mail,destination_label
info@vetrobalsamo.com,Microsoft 365
user@moretto.mobi,Server interno`}</pre>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            const input = e.target.elements.csvfile;
            if (input?.files?.[0]) onImportCsv(input.files[0]);
          }}
          className="form-grid"
        >
          <FormField label="File CSV" htmlFor="csvfile">
            <input
              id="csvfile"
              type="file"
              name="csvfile"
              accept=".csv,text/csv,text/plain"
              required
            />
          </FormField>
          <CheckboxField
            label="Salta la prima riga (intestazione colonne)"
            checked={importSkipHeader}
            onChange={setImportSkipHeader}
            disabled={importBusy}
          />
          <CheckboxField
            label="Aggiorna caselle già esistenti (stessa mail)"
            checked={importUpdateExisting}
            onChange={setImportUpdateExisting}
            disabled={importBusy}
          />
          <div className="modal-actions">
            <button type="button" className="btn-secondary" onClick={onClose} disabled={importBusy}>
              Annulla
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={importBusy || enabledDomains.length === 0}
            >
              {importBusy ? "Importazione..." : "Importa"}
            </button>
          </div>
        </form>

        {importResult && (
          <div className="import-summary">
            <p>
              Righe: {importResult.total_rows} · create: <strong>{importResult.created}</strong> ·
              aggiornate: <strong>{importResult.updated}</strong> · saltate:{" "}
              <strong>{importResult.skipped}</strong>
              {importResult.errors?.length > 0 && (
                <>
                  {" "}
                  · errori: <strong>{importResult.errors.length}</strong>
                </>
              )}
            </p>
            {importResult.errors?.length > 0 && (
              <ul className="import-errors">
                {importResult.errors.map((err, i) => (
                  <li key={i}>
                    riga {err.line} ({err.email}): {err.error}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
