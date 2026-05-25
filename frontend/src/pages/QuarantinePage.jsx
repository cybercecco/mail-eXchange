import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

const POLL_MS = 30_000;

function formatTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("it-IT");
  } catch {
    return iso;
  }
}

function formatCountdown(seconds) {
  if (seconds == null || seconds <= 0) return "Scaduto";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

export default function QuarantinePage({ onError }) {
  const [items, setItems] = useState([]);
  const [ttlHours, setTtlHours] = useState(36);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState({ from: "", to: "", q: "" });
  const [applied, setApplied] = useState({ from: "", to: "", q: "" });
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [message, setMessage] = useState("");

  const loadList = useCallback(
    async (silent = false) => {
      if (!silent) setLoading(true);
      try {
        const params = new URLSearchParams();
        if (applied.from) params.set("from", applied.from);
        if (applied.to) params.set("to", applied.to);
        if (applied.q) params.set("q", applied.q);
        const query = params.toString();
        const data = await api(`/quarantine${query ? `?${query}` : ""}`);
        setItems(data.items || []);
        setTtlHours(data.ttl_hours || 36);
        if (onError) onError("");
      } catch (err) {
        if (onError) onError(err.message || String(err));
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [applied, onError]
  );

  const loadDetail = useCallback(async (entryId) => {
    setDetailLoading(true);
    setDetail(null);
    try {
      const data = await api(`/quarantine/${encodeURIComponent(entryId)}`);
      setDetail(data);
    } catch (err) {
      if (onError) onError(err.message || String(err));
    } finally {
      setDetailLoading(false);
    }
  }, [onError]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  useEffect(() => {
    const timer = setInterval(() => loadList(true), POLL_MS);
    return () => clearInterval(timer);
  }, [loadList]);

  useEffect(() => {
    if (selected) {
      loadDetail(selected);
    } else {
      setDetail(null);
    }
  }, [selected, loadDetail]);

  function applyFilters(event) {
    event.preventDefault();
    setApplied({ ...filters });
    setSelected(null);
  }

  async function releaseEntry(entryId) {
    if (!window.confirm("Rilasciare il messaggio verso il destinatario originale?")) return;
    setActionBusy(true);
    setMessage("");
    try {
      const result = await api(`/quarantine/${encodeURIComponent(entryId)}/release`, {
        method: "POST"
      });
      setMessage(`Messaggio rilasciato verso ${(result.to || []).join(", ")}`);
      setSelected(null);
      await loadList(true);
      if (onError) onError("");
    } catch (err) {
      if (onError) onError(err.message || String(err));
    } finally {
      setActionBusy(false);
    }
  }

  async function deleteEntry(entryId) {
    if (!window.confirm("Eliminare definitivamente questo messaggio dalla quarantena?")) return;
    setActionBusy(true);
    setMessage("");
    try {
      await api(`/quarantine/${encodeURIComponent(entryId)}`, { method: "DELETE" });
      setMessage("Messaggio eliminato dalla quarantena");
      setSelected(null);
      await loadList(true);
      if (onError) onError("");
    } catch (err) {
      if (onError) onError(err.message || String(err));
    } finally {
      setActionBusy(false);
    }
  }

  return (
    <>
      <header className="page-header">
        <h2>Quarantena spam</h2>
        <p>
          Messaggi bloccati da Amavis conservati per {ttlHours} ore prima della cancellazione
          automatica. Ricerca per mittente/destinatario e rilascio manuale.
        </p>
      </header>

      <form className="panel quarantine-filters" onSubmit={applyFilters}>
        <h3>Ricerca</h3>
        <div className="quarantine-filter-grid">
          <label>
            Mittente
            <input
              type="text"
              value={filters.from}
              onChange={(e) => setFilters((prev) => ({ ...prev, from: e.target.value }))}
              placeholder="noreply@dominio.it"
            />
          </label>
          <label>
            Destinatario
            <input
              type="text"
              value={filters.to}
              onChange={(e) => setFilters((prev) => ({ ...prev, to: e.target.value }))}
              placeholder="utente@dominio.it"
            />
          </label>
          <label>
            Testo libero
            <input
              type="text"
              value={filters.q}
              onChange={(e) => setFilters((prev) => ({ ...prev, q: e.target.value }))}
              placeholder="oggetto, motivo..."
            />
          </label>
        </div>
        <div className="form-actions">
          <button type="submit" className="btn-primary">
            Cerca
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              setFilters({ from: "", to: "", q: "" });
              setApplied({ from: "", to: "", q: "" });
              setSelected(null);
            }}
          >
            Reset
          </button>
        </div>
      </form>

      {message && <div className="alert-success">{message}</div>}

      <div className="quarantine-layout">
        <div className="panel quarantine-list-panel">
          <h3>Messaggi in quarantena ({items.length})</h3>
          {loading ? (
            <p className="empty-state">Caricamento...</p>
          ) : items.length === 0 ? (
            <p className="empty-state">Nessun messaggio in quarantena.</p>
          ) : (
            <ul className="list quarantine-list">
              {items.map((item) => (
                <li key={item.id} className="list-item">
                  <button
                    type="button"
                    className={`quarantine-list-item${selected === item.id ? " active" : ""}`}
                    onClick={() => setSelected(item.id)}
                  >
                    <strong>{item.subject}</strong>
                    <span>
                      {item.from || "—"} → {(item.to || []).join(", ") || "—"}
                    </span>
                    <span className="quarantine-meta">
                      Score: {item.spam_score ?? "—"} · {item.reason} · scade tra{" "}
                      {formatCountdown(item.expires_in_seconds)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="panel quarantine-detail-panel">
          <h3>Dettaglio</h3>
          {!selected ? (
            <p className="empty-state">Seleziona un messaggio dalla lista.</p>
          ) : detailLoading || !detail ? (
            <p className="empty-state">Caricamento dettaglio...</p>
          ) : (
            <>
              <dl className="quarantine-detail-grid">
                <dt>Mittente</dt>
                <dd>{detail.from || "—"}</dd>
                <dt>Destinatari</dt>
                <dd>{(detail.to || []).join(", ") || "—"}</dd>
                <dt>Oggetto</dt>
                <dd>{detail.subject}</dd>
                <dt>Data</dt>
                <dd>{formatTime(detail.date)}</dd>
                <dt>Spam score</dt>
                <dd>{detail.spam_score ?? "—"}</dd>
                <dt>Motivo</dt>
                <dd>{detail.reason}</dd>
                <dt>Scadenza</dt>
                <dd>
                  {formatTime(detail.expires_at)} ({formatCountdown(detail.expires_in_seconds)})
                </dd>
                <dt>Dimensione</dt>
                <dd>{detail.size_bytes} byte</dd>
              </dl>

              <div className="quarantine-actions">
                <button
                  type="button"
                  className="btn-primary"
                  disabled={actionBusy}
                  onClick={() => releaseEntry(detail.id)}
                >
                  Rilascia verso destinatario
                </button>
                <button
                  type="button"
                  className="btn-danger"
                  disabled={actionBusy}
                  onClick={() => deleteEntry(detail.id)}
                >
                  Elimina
                </button>
              </div>

              <details className="quarantine-headers">
                <summary>Intestazioni ({detail.headers?.length || 0})</summary>
                <pre className="quarantine-headers-pre">
                  {(detail.headers || [])
                    .map((header) => `${header.name}: ${header.value}`)
                    .join("\n")}
                </pre>
              </details>
            </>
          )}
        </div>
      </div>
    </>
  );
}
