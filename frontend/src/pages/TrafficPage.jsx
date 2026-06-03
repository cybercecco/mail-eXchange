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

const WINDOW_METRICS = [
  {
    key: "ingresso",
    label: "Ingresso",
    hint: "Messaggi accettati da smtpd nella finestra (1 conteggio per queue ID).",
    color: "var(--accent)",
    queueType: "incoming"
  },
  {
    key: "bloccate",
    label: "Bloccate",
    hint: "Messaggi rifiutati o bloccati da Amavis/Postfix nella finestra (no warning di sistema).",
    color: "var(--status-err-fg)",
    queueType: "blocked"
  },
  {
    key: "in_uscita",
    label: "In uscita",
    hint: "Messaggi consegnati all'esterno (status=sent) nella finestra (1 conteggio per queue ID).",
    color: "var(--status-ok-fg)",
    queueType: "outgoing"
  }
];

const PIPELINE_QUEUES = [
  {
    key: "postfix_active",
    label: "Postfix attive",
    hint: "Messaggi in coda attiva Postfix (non ancora in uscita verso Amavis o destinazione).",
    queueType: "active",
    badgeClass: "traffic-queue-badge-active"
  },
  {
    key: "postfix_to_amavis",
    label: "Postfix → Amavis",
    hint: "Consegna attiva verso il filtro antispam/AV (porta 10024).",
    queueType: "active",
    badgeClass: "traffic-queue-badge-amavis"
  },
  {
    key: "postfix_outbound",
    label: "Postfix uscita",
    hint: "Consegna attiva verso server di destinazione esterni.",
    queueType: "active",
    badgeClass: "traffic-queue-badge-outbound"
  },
  {
    key: "postfix_deferred",
    label: "Postfix differite",
    hint: "Coda differita Postfix (retry programmati).",
    queueType: "deferred",
    badgeClass: "traffic-queue-badge-deferred"
  },
  {
    key: "postfix_hold",
    label: "Postfix hold",
    hint: "Messaggi in hold amministrativo.",
    queueType: "hold",
    badgeClass: "traffic-queue-badge-hold"
  },
  {
    key: "amavis",
    label: "Amavis",
    hint: "Messaggi in elaborazione Amavis (log recenti, ~3 min).",
    queueType: "active",
    badgeClass: "traffic-queue-badge-amavis"
  },
  {
    key: "clamav",
    label: "ClamAV",
    hint: "Fase scansione antivirus ClamAV dentro Amavis.",
    queueType: "active",
    badgeClass: "traffic-queue-badge-clamav"
  },
  {
    key: "spamassassin",
    label: "SpamAssassin",
    hint: "Fase analisi antispam SpamAssassin dentro Amavis.",
    queueType: "active",
    badgeClass: "traffic-queue-badge-spam"
  }
];

const POSTFIX_QUEUE_DETAIL = [
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

  const values = WINDOW_METRICS.map((metric) => stats?.[metric.key] ?? 0);
  const maxValue = Math.max(...values, 1);

  const sources = stats?.sources;
  const sourcesReady = sources && Object.values(sources).some(Boolean);
  const pipeline = queueSnapshot?.pipeline ?? stats?.pipeline ?? {};
  const liveCounts = queueSnapshot ?? stats?.queue_detail;
  const liveUpdatedAt = queueSnapshot?.pipeline_updated_at ?? queueSnapshot?.updated_at;
  const liveTotal =
    queueSnapshot?.total ??
    PIPELINE_QUEUES.reduce((sum, item) => sum + (pipeline[item.key] ?? 0), 0);
  const windowLabelActive = stats?.window_minutes
    ? windowLabel(stats.window_minutes)
    : windowLabel(windowMinutes);

  return (
    <>
      <header className="page-header page-header-row">
        <div>
          <h2>Traffico mail</h2>
          <p>
            Code in transito (Postfix, Amavis, ClamAV, SpamAssassin) aggiornate ogni ~5 s. Ingresso,
            bloccate e uscita contano messaggi unici nella finestra temporale selezionata.
            Finestra statistiche: ultimi {windowLabelActive}
            {stats?.window_minutes && stats.window_minutes !== windowMinutes ? " (aggiornamento…)" : ""}.
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
            <h3>Code in transito (tempo reale)</h3>
            <p className="traffic-live-meta">
              Postfix, Amavis, ClamAV e SpamAssassin — snapshot ogni ~5 s
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
            <span>Totale in transito</span>
            <strong>{liveTotal}</strong>
          </div>
        </div>

        {snapshotError && <div className="alert-error">{snapshotError}</div>}

        <div className="traffic-live-badges traffic-pipeline-badges">
          {PIPELINE_QUEUES.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`traffic-live-badge traffic-clickable ${item.badgeClass}`}
              onClick={() => openQueueDetail(item.queueType)}
              title={item.hint}
            >
              <span className="traffic-live-badge-label">{item.label}</span>
              <strong className="traffic-live-badge-value">
                {pipeline[item.key] ?? (snapshotLoading ? "…" : 0)}
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
        {WINDOW_METRICS.map((metric) => (
          <button
            key={metric.key}
            type="button"
            className="traffic-card traffic-clickable"
            onClick={() => openQueueDetail(metric.queueType)}
            title={metric.hint || `Apri contenuto: ${metric.label}`}
          >
            <span className="traffic-card-label">{metric.label}</span>
            <span className="traffic-card-scope">finestra {windowLabelActive}</span>
            <strong className="traffic-card-value">{stats?.[metric.key] ?? (loading ? "…" : 0)}</strong>
          </button>
        ))}
        <div className="traffic-card traffic-card-static" title="Somma di tutte le code in transito (tempo reale)">
          <span className="traffic-card-label">In transito ora</span>
          <span className="traffic-card-scope">tempo reale</span>
          <strong className="traffic-card-value">{stats?.in_coda ?? liveTotal ?? (loading ? "…" : 0)}</strong>
        </div>
      </div>

      <div className="panel traffic-chart-panel">
        <h3>Andamento transito ({windowLabelActive})</h3>
        <p className="panel-hint traffic-chart-hint">
          Conteggi storici per messaggio unico nella finestra selezionata. Le code live sono nella
          sezione sopra.
        </p>
        <div className="traffic-chart" role="img" aria-label="Grafico a barre del traffico mail">
          {WINDOW_METRICS.map((metric) => {
            const value = stats?.[metric.key] ?? 0;
            const height = Math.max(4, Math.round((value / maxValue) * 100));
            return (
              <button
                key={metric.key}
                type="button"
                className="traffic-bar-col traffic-clickable"
                onClick={() => openQueueDetail(metric.queueType)}
                title={metric.hint || `Apri contenuto: ${metric.label}`}
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
          <h3>Dettaglio coda Postfix (snapshot)</h3>
          <ul className="list-items traffic-queue-detail">
            {POSTFIX_QUEUE_DETAIL.map((item) => (
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
