import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

function daemonBadgeStatus(status) {
  if (status === "ok") return "ok";
  if (status === "down") return "error";
  return "warning";
}

function daemonStatusLabel(status) {
  if (status === "ok") return "Operativo";
  if (status === "down") return "Non operativo";
  return "Sconosciuto";
}

function daemonShortLabel(item) {
  const map = {
    api: "API",
    frontend: "Frontend",
    caddy: "Caddy",
    postfix: "Postfix",
    amavis: "Amavis",
    clamav: "ClamAV",
    opendkim: "OpenDKIM",
  };
  return map[item.id] || item.label;
}

export default function SystemPage({ user, onLogout }) {
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState("");
  const [daemons, setDaemons] = useState(null);
  const [daemonsError, setDaemonsError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [restarting, setRestarting] = useState({});

  const isAdmin = user?.role === "admin";

  const loadDaemons = useCallback(async () => {
    setRefreshing(true);
    try {
      const data = await api("/system/daemons");
      setDaemons(data);
      setDaemonsError("");
    } catch (err) {
      setDaemonsError(err.message);
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await api("/health");
        if (!cancelled) {
          setHealth(data);
          setHealthError("");
        }
      } catch (err) {
        if (!cancelled) setHealthError(err.message);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    loadDaemons();
    const timer = setInterval(loadDaemons, 30_000);
    return () => clearInterval(timer);
  }, [loadDaemons]);

  async function handleRestart(item) {
    const apiWarning =
      "Riavviando l'API la sessione corrente potrebbe interrompersi. Continuare?";
    const confirmMsg =
      item.id === "api" ? apiWarning : `Riavviare ${item.label}?`;
    if (!confirm(confirmMsg)) return;

    setRestarting((prev) => ({ ...prev, [item.id]: true }));
    try {
      const result = await api(`/system/daemons/${item.id}/restart`, {
        method: "POST",
      });
      if (result?.warning) {
        alert(result.warning);
      }
      setTimeout(loadDaemons, 3000);
    } catch (err) {
      alert(err.message);
    } finally {
      setRestarting((prev) => ({ ...prev, [item.id]: false }));
    }
  }

  const summary = daemons?.summary;

  return (
    <>
      <header className="page-header">
        <h2>Stato & sessione</h2>
        <p>
          Stato operativo dei servizi dello stack mail e informazioni sulla sessione corrente.
        </p>
      </header>

      <div className="panel">
        <div className="panel-actions" style={{ marginBottom: "0.75rem" }}>
          <h3 className="panel-actions__title" style={{ margin: 0 }}>
            Demone e servizi
          </h3>
          <button
            type="button"
            className="btn-secondary btn-sm"
            onClick={loadDaemons}
            disabled={refreshing}
          >
            {refreshing ? "Aggiornamento..." : "Aggiorna"}
          </button>
        </div>
        {daemonsError ? (
          <p className="auth-error">{daemonsError}</p>
        ) : !daemons ? (
          <p className="empty-state">Verifica servizi in corso...</p>
        ) : (
          <>
            {summary && (
              <p
                className={
                  daemons.status === "ok" ? "health-ok panel-hint" : "alert-warn"
                }
                style={{ marginTop: 0 }}
              >
                {daemons.status === "ok" ? (
                  <>
                    <span className="health-dot" />
                    Tutti i servizi operativi ({summary.operational}/{summary.total})
                  </>
                ) : (
                  <>
                    {summary.down} servizio/i non operativi su {summary.total}
                  </>
                )}
              </p>
            )}
            <div className="daemon-chips">
              {daemons.daemons.map((item) => {
                const chipStatus = daemonBadgeStatus(item.status);
                const busy = Boolean(restarting[item.id]);
                return (
                  <div
                    key={item.id}
                    className={`daemon-chip daemon-chip--${chipStatus}${busy ? " daemon-chip--busy" : ""}`}
                    title={item.detail ? `${item.role} · ${item.detail}` : item.role}
                  >
                    <span className="daemon-chip__label">{daemonShortLabel(item)}</span>
                    <span className="daemon-chip__status">{daemonStatusLabel(item.status)}</span>
                    {isAdmin && item.restartable && (
                      <button
                        type="button"
                        className="daemon-chip__restart btn-secondary btn-sm"
                        onClick={() => handleRestart(item)}
                        disabled={busy || refreshing}
                      >
                        {busy ? "..." : "Riavvia"}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}
        {health && !healthError && (
          <p className="panel-hint" style={{ marginBottom: 0, marginTop: "0.75rem" }}>
            Endpoint pubblico API: {health.status || "ok"}
          </p>
        )}
        {healthError && (
          <p className="auth-error" style={{ marginTop: "0.75rem" }}>
            {healthError}
          </p>
        )}
      </div>

      <div className="panel">
        <h3>Sessione</h3>
        <ul className="list-items" style={{ marginTop: 0 }}>
          <li className="list-item" style={{ paddingTop: 0 }}>
            <div className="list-item-meta">
              <strong>{user.username}</strong>
              <span>
                Ruolo: {user.role}
                {user.mfa_enabled ? " · MFA attivo" : " · MFA non attivo"}
              </span>
            </div>
          </li>
        </ul>
        <button type="button" className="btn-danger" onClick={onLogout} style={{ marginTop: "0.75rem" }}>
          Termina sessione
        </button>
      </div>
    </>
  );
}
