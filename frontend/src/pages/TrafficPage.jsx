import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import QueueContentModal from "../components/QueueContentModal";

const STATS_POLL_MS = 30_000;
const SNAPSHOT_POLL_MS = 5_000;

const TIME_WINDOWS = [
  { value: 15, label: "15 min" },
  { value: 60, label: "1 h" },
  { value: 360, label: "6 h" },
  { value: 1440, label: "24 h" }
];

const METRICS = [
  { key: "ingresso", label: "Ingresso", color: "var(--accent)", queueType: "incoming" },
  { key: "in_coda", label: "In coda (antispam/AV)", color: "var(--status-warn-fg)", queueType: "active" },
  { key: "bloccate", label: "Bloccate", color: "var(--status-err-fg)", queueType: "blocked" },
  { key: "in_uscita", label: "In uscita", color: "var(--status-ok-fg)", queueType: "outgoing" }
];

const QUEUE_DETAIL = [
  { key: "active", label: "Attive", queueType: "active", badgeClass: "traffic-queue-badge-active" },
  { key: "deferred", label: "Differite", queueType: "deferred", badgeClass: "traffic-queue-badge-deferred" },
  { key: "hold", label: "In hold", queueType: "hold", badgeClass: "traffic-queue-badge-hold" }
];

function formatTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("it-IT");
  } catch {
    return iso;
  }
}

function windowLabel(minutes) {
  const preset = TIME_WINDOWS.find((item) => item.value === minutes);
  if (preset) return preset.label;
  if (minutes < 60) return `${minutes} min`;
  if (minutes % 60 === 0) return `${minutes / 60} h`;
  return `${minutes} min`;
}

export default function TrafficPage({ isAdmin }) {
  const [windowMinutes, setWindowMinutes] = useState(60);
  const [stats, setStats] = useState(null);
  const [queueSnapshot, setQueueSnapshot] = useState(null);
  const [loading, setLoading] = useState(true);
  const [snapshotLoading, setSnapshotLoading] = useState(true);
  const [error, setError] = useState("");
  const [snapshotError, setSnapshotError] = useState("");
  const [queueOpen, setQueueOpen] = useState(false);
  const [queueLoading, setQueueLoading] = useState(false);
  const [queueError, setQueueError] = useState("");
  const [queueData, setQueueData] = useState(null);
  const [queueActionBusy, setQueueActionBusy] = useState(false);
  const [queueActionMessage, setQueueActionMessage] = useState("");
  const [activeQueueType, setActiveQueueType] = useState("active");

  const loadStats = useCallback(
    async (silent = false) => {
      if (!silent) setLoading(true);
      try {
        const data = await api(`/stats/traffic?window_minutes=${windowMinutes}`);
        setStats(data);
        setError("");
      } catch (err) {
        setError(err.message);
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [windowMinutes]
  );

  const loadQueueSnapshot = useCallback(async (silent = false) => {
    if (!silent) setSnapshotLoading(true);
    try {
      const data = await api("/stats/queue/snapshot");
      setQueueSnapshot(data);
      setSnapshotError("");
    } catch (err) {
      setSnapshotError(err.message);
    } finally {
      if (!silent) setSnapshotLoading(false);
    }
  }, []);

  const loadQueueData = useCallback(
    async (queueType) => {
      setQueueLoading(true);
      setQueueError("");
      try {
        const data = await api(
          `/stats/queue?type=${encodeURIComponent(queueType)}&window_minutes=${windowMinutes}`
        );
        setQueueData(data);
      } catch (err) {
        setQueueError(err.message);
      } finally {
        setQueueLoading(false);
      }
    },
    [windowMinutes]
  );

  const openQueueDetail = useCallback(
    async (queueType) => {
      setActiveQueueType(queueType);
      setQueueOpen(true);
      setQueueData(null);
      setQueueActionMessage("");
      await loadQueueData(queueType);
    },
    [loadQueueData]
  );

  const closeQueueDetail = useCallback(() => {
    if (queueLoading || queueActionBusy) return;
    setQueueOpen(false);
    setQueueData(null);
    setQueueError("");
    setQueueActionMessage("");
  }, [queueLoading, queueActionBusy]);

  const waitForQueueCommand = useCallback(async () => {
    await new Promise((resolve) => setTimeout(resolve, 6000));
  }, []);

  const refreshAfterAction = useCallback(async () => {
    await waitForQueueCommand();
    await Promise.all([
      loadQueueSnapshot(true),
      loadStats(true),
      queueOpen ? loadQueueData(activeQueueType) : Promise.resolve()
    ]);
  }, [
    activeQueueType,
    loadQueueData,
    loadQueueSnapshot,
    loadStats,
    queueOpen,
    waitForQueueCommand
  ]);

  const handleFlush = useCallback(
    async (queueType) => {
      const target = queueType === "all" ? "deferred" : queueType;
      if (!confirm(`Eseguire flush immediato sulla coda ${target}?`)) return;
      setQueueActionBusy(true);
      setQueueActionMessage("");
      try {
        await api("/stats/queue/flush", {
          method: "POST",
          body: JSON.stringify({ queue_type: target })
        });
        setQueueActionMessage("Flush inviato a Postfix. Aggiornamento tra pochi secondi...");
        await refreshAfterAction();
      } catch (err) {
        setQueueActionMessage(err.message);
      } finally {
        setQueueActionBusy(false);
      }
    },
    [refreshAfterAction]
  );

  const handleDeleteSelected = useCallback(
    async (queueIds) => {
      if (!queueIds.length) return;
      if (!confirm(`Eliminare ${queueIds.length} messaggi selezionati?`)) return;
      setQueueActionBusy(true);
      setQueueActionMessage("");
      try {
        await api("/stats/queue/delete", {
          method: "POST",
          body: JSON.stringify({ queue_ids: queueIds, delete_all: false, queue_type: activeQueueType })
        });
        setQueueActionMessage("Eliminazione inviata. Aggiornamento tra pochi secondi...");
        await refreshAfterAction();
      } catch (err) {
        setQueueActionMessage(err.message);
      } finally {
        setQueueActionBusy(false);
      }
    },
    [activeQueueType, refreshAfterAction]
  );

  const handleDeleteAll = useCallback(
    async (queueType) => {
      setQueueActionBusy(true);
      setQueueActionMessage("");
      try {
        const target = queueType === "all" ? "all" : queueType;
        await api("/stats/queue/delete", {
          method: "POST",
          body: JSON.stringify({ delete_all: true, queue_type: target, queue_ids: [] })
        });
        setQueueActionMessage("Eliminazione massiva inviata. Aggiornamento tra pochi secondi...");
        await refreshAfterAction();
      } catch (err) {
        setQueueActionMessage(err.message);
      } finally {
        setQueueActionBusy(false);
      }
    },
    [refreshAfterAction]
  );

  const handleQueueControl = useCallback(
    async (endpoint, confirmMessage, successMessage) => {
      if (!confirm(confirmMessage)) return;
      setQueueActionBusy(true);
      setQueueActionMessage("");
      try {
        await api(`/stats/queue/${endpoint}`, { method: "POST", body: "{}" });
        setQueueActionMessage(successMessage);
        await refreshAfterAction();
      } catch (err) {
        setQueueActionMessage(err.message);
      } finally {
        setQueueActionBusy(false);
      }
    },
    [refreshAfterAction]
  );

  useEffect(() => {
    loadStats();
    const timer = setInterval(() => loadStats(true), STATS_POLL_MS);
    return () => clearInterval(timer);
  }, [loadStats]);

  useEffect(() => {
    loadQueueSnapshot();
    const timer = setInterval(() => loadQueueSnapshot(true), SNAPSHOT_POLL_MS);
    return () => clearInterval(timer);
  }, [loadQueueSnapshot]);

  const values = METRICS.map((metric) => stats?.[metric.key] ?? 0);
  const maxValue = Math.max(...values, 1);

  const sources = stats?.sources;
  const sourcesReady = sources && Object.values(sources).some(Boolean);
  const liveCounts = queueSnapshot ?? stats?.queue_detail;
  const liveUpdatedAt = queueSnapshot?.updated_at;
  const liveTotal =
    queueSnapshot?.total ??
    (liveCounts
      ? (liveCounts.active ?? 0) + (liveCounts.deferred ?? 0) + (liveCounts.hold ?? 0)
      : 0);

  return (
    <>
      <header className="page-header page-header-row">
        <div>
          <h2>Traffico mail</h2>
          <p>
            Conteggi recenti da log Postfix/Amavis e stato code in tempo reale. Clicca su card,
            barre o badge coda per vedere i messaggi. Finestra attuale: ultimi{" "}
            {windowLabel(windowMinutes)}.
          </p>
        </div>
        <div className="page-header-actions traffic-header-actions">
          <label className="traffic-window-select">
            <span>Finestra</span>
            <select
              value={windowMinutes}
              onChange={(e) => setWindowMinutes(Number(e.target.value))}
              disabled={loading}
            >
              {TIME_WINDOWS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          {isAdmin && (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => openQueueDetail("all")}
              disabled={loading}
            >
              Tutte le code
            </button>
          )}
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              loadStats();
              loadQueueSnapshot();
            }}
            disabled={loading || snapshotLoading}
          >
            {loading || snapshotLoading ? "Aggiornamento..." : "Aggiorna"}
          </button>
        </div>
      </header>

      {error && <div className="alert-error">{error}</div>}

      {!sourcesReady && stats && (
        <div className="alert-warn">
          Log o snapshot coda non ancora disponibili. Riavvia i container Postfix/Amavis dopo il
          deploy per abilitare la raccolta su volume condiviso.
        </div>
      )}

      <div className="panel traffic-live-panel">
        <div className="traffic-live-header">
          <div>
            <h3>Code Postfix (tempo reale)</h3>
            <p className="traffic-live-meta">
              Snapshot ogni ~5 s da mx-postfix
              {liveUpdatedAt ? (
                <>
                  {" · "}
                  <strong>Aggiornato: {formatTime(liveUpdatedAt)}</strong>
                </>
              ) : snapshotLoading ? (
                " · caricamento..."
              ) : (
                " · in attesa del primo snapshot"
              )}
            </p>
          </div>
          <div className="traffic-live-total">
            <span>Totale messaggi</span>
            <strong>{liveTotal}</strong>
          </div>
        </div>

        {snapshotError && <div className="alert-error">{snapshotError}</div>}

        <div className="traffic-live-badges">
          {QUEUE_DETAIL.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`traffic-live-badge traffic-clickable ${item.badgeClass}`}
              onClick={() => openQueueDetail(item.queueType)}
              title={`Apri contenuto: ${item.label}`}
            >
              <span className="traffic-live-badge-label">{item.label}</span>
              <strong className="traffic-live-badge-value">
                {liveCounts?.[item.key] ?? (snapshotLoading ? "…" : 0)}
              </strong>
            </button>
          ))}
        </div>

        {isAdmin && (
          <div className="traffic-control-section">
            <p className="traffic-control-title">Controllo flusso (admin)</p>
            <div className="traffic-control-actions">
              <button
                type="button"
                className="btn-secondary btn-sm"
                disabled={queueActionBusy}
                title="postsuper -h ALL — mette in hold tutta la posta in uscita"
                onClick={() =>
                  handleQueueControl(
                    "hold",
                    "Mettere in hold tutta la posta in uscita (postsuper -h ALL)?",
                    "Hold uscita inviato. Aggiornamento tra pochi secondi..."
                  )
                }
              >
                Pausa uscita
              </button>
              <button
                type="button"
                className="btn-secondary btn-sm"
                disabled={queueActionBusy}
                title="postsuper -r ALL — rilascia i messaggi in hold"
                onClick={() =>
                  handleQueueControl(
                    "release",
                    "Rilasciare tutti i messaggi attualmente in hold (postsuper -r ALL)?",
                    "Rilascio hold inviato. Aggiornamento tra pochi secondi..."
                  )
                }
              >
                Rilascia hold
              </button>
              <button
                type="button"
                className="btn-danger btn-sm"
                disabled={queueActionBusy}
                title="postfix pause — blocca accettazione e consegna"
                onClick={() =>
                  handleQueueControl(
                    "pause",
                    "Mettere in pausa TUTTO Postfix (nessuna accettazione né consegna)?",
                    "Pausa Postfix inviata. Aggiornamento tra pochi secondi..."
                  )
                }
              >
                Pausa totale Postfix
              </button>
              <button
                type="button"
                className="btn-primary btn-sm"
                disabled={queueActionBusy}
                title="postfix resume — riprende operatività normale"
                onClick={() =>
                  handleQueueControl(
                    "resume",
                    "Riprendere operatività Postfix (postfix resume)?",
                    "Ripresa Postfix inviata. Aggiornamento tra pochi secondi..."
                  )
                }
              >
                Riprendi
              </button>
            </div>
            {queueActionMessage && !queueOpen && (
              <p className="panel-hint">{queueActionMessage}</p>
            )}
          </div>
        )}
      </div>

      <div className="traffic-cards">
        {METRICS.map((metric) => (
          <button
            key={metric.key}
            type="button"
            className="traffic-card traffic-clickable"
            onClick={() => openQueueDetail(metric.queueType)}
            title={`Apri contenuto: ${metric.label}`}
          >
            <span className="traffic-card-label">{metric.label}</span>
            <strong className="traffic-card-value">{stats?.[metric.key] ?? (loading ? "…" : 0)}</strong>
          </button>
        ))}
      </div>

      <div className="panel traffic-chart-panel">
        <h3>Andamento ({windowLabel(windowMinutes)})</h3>
        <div className="traffic-chart" role="img" aria-label="Grafico a barre del traffico mail">
          {METRICS.map((metric) => {
            const value = stats?.[metric.key] ?? 0;
            const height = Math.max(4, Math.round((value / maxValue) * 100));
            return (
              <button
                key={metric.key}
                type="button"
                className="traffic-bar-col traffic-clickable"
                onClick={() => openQueueDetail(metric.queueType)}
                title={`Apri contenuto: ${metric.label}`}
              >
                <div className="traffic-bar-track">
                  <div
                    className="traffic-bar-fill"
                    style={{ height: `${height}%`, background: metric.color }}
                  />
                </div>
                <span className="traffic-bar-value">{value}</span>
                <span className="traffic-bar-label">{metric.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {stats?.queue_detail && (
        <div className="panel">
          <h3>Dettaglio coda Postfix (da statistiche)</h3>
          <ul className="list-items traffic-queue-detail">
            {QUEUE_DETAIL.map((item) => (
              <li key={item.key} className="list-item">
                <button
                  type="button"
                  className="traffic-queue-item traffic-clickable"
                  onClick={() => openQueueDetail(item.queueType)}
                  title={`Apri contenuto: ${item.label}`}
                >
                  <span>{item.label}</span>
                  <strong>{stats.queue_detail[item.key]}</strong>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="traffic-meta">
        Ultimo aggiornamento statistiche: {formatTime(stats?.collected_at)}
        {stats?.sources ? (
          <>
            {" · "}
            Fonti: Postfix {stats.sources.postfix_log ? "ok" : "assente"}, Amavis{" "}
            {stats.sources.amavis_log ? "ok" : "assente"}, coda{" "}
            {stats.sources.queue_snapshot ? "ok" : "assente"}
          </>
        ) : null}
      </p>

      <QueueContentModal
        open={queueOpen}
        loading={queueLoading}
        error={queueError}
        data={queueData}
        onClose={closeQueueDetail}
        isAdmin={isAdmin}
        actionBusy={queueActionBusy}
        actionMessage={queueActionMessage}
        onFlush={handleFlush}
        onDeleteSelected={handleDeleteSelected}
        onDeleteAll={handleDeleteAll}
        onRefresh={() => loadQueueData(activeQueueType)}
      />
    </>
  );
}
