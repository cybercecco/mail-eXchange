import { useEffect, useMemo, useState } from "react";

function formatTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("it-IT");
  } catch {
    return iso;
  }
}

function formatAge(seconds) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h`;
  return `${Math.floor(seconds / 86400)} g`;
}

function formatSize(bytes) {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatRecipients(to) {
  if (!to || to.length === 0) return "—";
  return to.join(", ");
}

function isQueueRow(type) {
  return ["active", "deferred", "hold", "all"].includes(type);
}

function rowDetailText(row) {
  const text = row.reason || row.summary;
  return text && String(text).trim() ? String(text).trim() : null;
}

export default function QueueContentModal({
  open,
  loading,
  error,
  data,
  onClose,
  isAdmin,
  actionBusy,
  actionMessage,
  onFlush,
  onDeleteSelected,
  onDeleteAll,
  onRefresh
}) {
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [detailMessage, setDetailMessage] = useState(null);

  const messages = data?.messages ?? [];
  const queueRows = isQueueRow(data?.type);
  const selectableIds = useMemo(
    () => messages.map((row) => row.queue_id).filter(Boolean),
    [messages]
  );

  useEffect(() => {
    if (!open) {
      setSelectedIds(new Set());
      setDetailMessage(null);
    }
  }, [open, data?.type]);

  useEffect(() => {
    if (!open) return;
    function onKey(e) {
      if (e.key !== "Escape" || loading || actionBusy) return;
      if (detailMessage) {
        setDetailMessage(null);
        return;
      }
      onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, loading, actionBusy, onClose, detailMessage]);

  if (!open) return null;

  const allSelected =
    selectableIds.length > 0 && selectableIds.every((id) => selectedIds.has(id));

  function toggleOne(queueId) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(queueId)) {
        next.delete(queueId);
      } else {
        next.add(queueId);
      }
      return next;
    });
  }

  function toggleAll() {
    if (allSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(selectableIds));
    }
  }

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget && !loading && !actionBusy) onClose();
      }}
    >
      <div
        className="modal-dialog modal-dialog-wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="queue-content-title"
      >
        <div className="modal-header">
          <div>
            <h3 id="queue-content-title">Contenuto coda</h3>
            {data?.label ? <p className="modal-subtitle">{data.label}</p> : null}
          </div>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            disabled={loading || actionBusy}
            aria-label="Chiudi"
          >
            ×
          </button>
        </div>

        {isAdmin && queueRows && (
          <div className="queue-actions-bar">
            <button
              type="button"
              className="btn-secondary btn-sm"
              disabled={loading || actionBusy}
              onClick={() => onFlush?.(data?.type)}
              title="Tenta consegna immediata (postqueue -f)"
            >
              Flush immediato
            </button>
            <button
              type="button"
              className="btn-danger btn-sm"
              disabled={loading || actionBusy || selectedIds.size === 0}
              onClick={() => onDeleteSelected?.(Array.from(selectedIds))}
            >
              Elimina selezionate ({selectedIds.size})
            </button>
            <button
              type="button"
              className="btn-danger btn-sm"
              disabled={loading || actionBusy || messages.length === 0}
              onClick={() => {
                if (
                  confirm(
                    `Eliminare tutti i messaggi in coda (${data?.label || data?.type})?`
                  )
                ) {
                  onDeleteAll?.(data?.type);
                }
              }}
            >
              Elimina tutte
            </button>
            <button
              type="button"
              className="btn-secondary btn-sm"
              disabled={loading || actionBusy}
              onClick={onRefresh}
            >
              Aggiorna
            </button>
          </div>
        )}
        {isAdmin && queueRows && (
          <p className="panel-hint queue-control-hint">
            Pausa uscita, hold e pausa totale Postfix sono nel pannello &quot;Code Postfix (tempo
            reale)&quot; sulla pagina Traffico.
          </p>
        )}
        {actionMessage && <p className="panel-hint">{actionMessage}</p>}

        {loading && <p className="queue-modal-state">Caricamento...</p>}
        {!loading && error && <div className="alert-error">{error}</div>}
        {!loading && !error && !data?.source_available && (
          <div className="alert-warn">
            Dati non ancora disponibili. Verifica che Postfix stia scrivendo lo snapshot su volume
            condiviso.
          </div>
        )}
        {!loading && !error && data?.source_available && messages.length === 0 && (
          <p className="queue-modal-state">Nessun messaggio trovato.</p>
        )}

        {!loading && !error && messages.length > 0 && (
          <div className="queue-table-wrap">
            <table className="queue-table">
              <thead>
                <tr>
                  {queueRows && isAdmin ? (
                    <th>
                      <input
                        type="checkbox"
                        checked={allSelected}
                        onChange={toggleAll}
                        aria-label="Seleziona tutte"
                        disabled={actionBusy || selectableIds.length === 0}
                      />
                    </th>
                  ) : null}
                  {queueRows ? (
                    <>
                      <th>ID coda</th>
                      <th>Mittente</th>
                      <th>Destinatario</th>
                      <th>Dimensione</th>
                      <th>Stato</th>
                      <th>Età</th>
                    </>
                  ) : (
                    <>
                      <th>Ora</th>
                      <th>Fonte</th>
                      <th>Mittente</th>
                      <th>Destinatario</th>
                      <th>Dettaglio</th>
                    </>
                  )}
                </tr>
              </thead>
              <tbody>
                {messages.map((row, index) =>
                  queueRows ? (
                    <tr key={`${row.queue_id}-${index}`}>
                      {isAdmin ? (
                        <td>
                          {row.queue_id ? (
                            <input
                              type="checkbox"
                              checked={selectedIds.has(row.queue_id)}
                              onChange={() => toggleOne(row.queue_id)}
                              aria-label={`Seleziona ${row.queue_id}`}
                              disabled={actionBusy}
                            />
                          ) : null}
                        </td>
                      ) : null}
                      <td className="mono">{row.queue_id || "—"}</td>
                      <td>{row.from || "—"}</td>
                      <td>{formatRecipients(row.to)}</td>
                      <td>{formatSize(row.size_bytes)}</td>
                      <td>{row.status || "—"}</td>
                      <td title={row.arrival || undefined}>{formatAge(row.age_seconds)}</td>
                    </tr>
                  ) : (
                    <tr key={`${row.queue_id || row.timestamp}-${index}`}>
                      <td>{formatTime(row.timestamp)}</td>
                      <td>{row.source || "—"}</td>
                      <td>{row.from || row.client || "—"}</td>
                      <td>{formatRecipients(row.to)}</td>
                      <QueueDetailCell row={row} onOpen={setDetailMessage} />
                    </tr>
                  )
                )}
              </tbody>
            </table>
          </div>
        )}

        {!loading && data && (
          <p className="queue-modal-meta">
            {messages.length} elementi
            {data.window_minutes ? ` · finestra ${data.window_minutes} min` : ""}
            {data.updated_at ? ` · snapshot ${formatTime(data.updated_at)}` : ""}
          </p>
        )}
      </div>

      {detailMessage ? (
        <div
          className="modal-overlay modal-overlay-nested"
          role="presentation"
          onClick={(e) => {
            if (e.target === e.currentTarget) setDetailMessage(null);
          }}
        >
          <div
            className="modal-dialog modal-dialog-detail"
            role="dialog"
            aria-modal="true"
            aria-labelledby="queue-detail-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <h3 id="queue-detail-title">Dettaglio messaggio</h3>
              <button
                type="button"
                className="modal-close"
                onClick={() => setDetailMessage(null)}
                aria-label="Chiudi dettaglio"
              >
                ×
              </button>
            </div>
            <pre className="queue-detail-pre">{detailMessage}</pre>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function QueueDetailCell({ row, onOpen }) {
  const detail = rowDetailText(row);
  if (!detail) {
    return <td className="queue-table-detail">—</td>;
  }

  return (
    <td
      className="queue-table-detail queue-table-detail--clickable"
      title={`${detail}\n\nClicca per dettaglio`}
      role="button"
      tabIndex={0}
      onClick={(e) => {
        e.stopPropagation();
        onOpen(detail);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          e.stopPropagation();
          onOpen(detail);
        }
      }}
    >
      <span className="queue-table-detail-text">{detail}</span>
      <span className="queue-table-detail-hint" aria-hidden="true">
        Clicca per dettaglio
      </span>
    </td>
  );
}
