import { useEffect, useState } from "react";
import { api } from "../api";
import AddDomainModal from "../components/AddDomainModal";
import DomainDnsSettingsPanel from "../components/DomainDnsSettingsPanel";
import DomainMailboxesPanel from "../components/DomainMailboxesPanel";
import { CheckboxField, FormField } from "../components/FormField";

function destinationLabel(dest) {
  const base = `${dest.host}:${dest.port}`;
  const imapHost = dest.imap_auth_host || dest.host;
  const imapPort = dest.imap_auth_port || 993;
  const imapSuffix = imapHost !== dest.host || imapPort !== 993 ? ` · IMAP ${imapHost}:${imapPort}` : "";
  const label = dest.label && dest.label !== dest.host ? `${dest.label} — ${base}` : base;
  return `${label}${imapSuffix}`;
}

const emptyDestForm = {
  label: "",
  host: "",
  port: "25",
  imap_auth_host: "",
  imap_auth_port: ""
};

const SETTINGS_TABS = [
  { id: "generale", label: "Generale" },
  { id: "destinazioni", label: "Destinazioni" },
  { id: "caselle", label: "Caselle" },
  { id: "cluster", label: "Cluster" },
  { id: "relay", label: "Relay" },
  { id: "dns", label: "DNS" }
];

export default function DomainsPage({
  domains,
  domainForm,
  setDomainForm,
  onAddDomain,
  onToggleDomain,
  onDeleteDomain,
  onRefresh,
  onSyncWarning,
  onDomainTabChange,
  openSettingsTab,
  onOpenSettingsTabConsumed,
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
  const [destForms, setDestForms] = useState({});
  const [destBusy, setDestBusy] = useState(null);
  const [editingDestKey, setEditingDestKey] = useState(null);
  const [destEditDrafts, setDestEditDrafts] = useState({});
  const [siblingDrafts, setSiblingDrafts] = useState({});
  const [syncSecretDrafts, setSyncSecretDrafts] = useState({});
  const [siblingBusy, setSiblingBusy] = useState(null);
  const [relayBusy, setRelayBusy] = useState(null);
  const [relaySourceDrafts, setRelaySourceDrafts] = useState({});
  const [relaySourceBusy, setRelaySourceBusy] = useState(null);
  const [activeTabId, setActiveTabId] = useState(() => domains[0]?.id ?? null);
  const [settingsTabByDomain, setSettingsTabByDomain] = useState({});
  const [addDomainOpen, setAddDomainOpen] = useState(false);
  const [addDomainBusy, setAddDomainBusy] = useState(false);

  useEffect(() => {
    setActiveTabId((current) => {
      if (domains.length === 0) return null;
      if (current != null && domains.some((d) => d.id === current)) return current;
      return domains[0].id;
    });
  }, [domains]);

  useEffect(() => {
    if (activeTabId != null) {
      onDomainTabChange?.(activeTabId);
    }
  }, [activeTabId, onDomainTabChange]);

  useEffect(() => {
    if (!openSettingsTab || activeTabId == null) return;
    setSettingsTab(activeTabId, openSettingsTab);
    onOpenSettingsTabConsumed?.();
  }, [openSettingsTab, activeTabId, onOpenSettingsTabConsumed]);

  function settingsTabFor(domainId) {
    return settingsTabByDomain[domainId] || SETTINGS_TABS[0].id;
  }

  function setSettingsTab(domainId, tabId) {
    setSettingsTabByDomain((prev) => ({ ...prev, [domainId]: tabId }));
  }

  function destFormFor(domainId) {
    return destForms[domainId] || emptyDestForm;
  }

  function setDestFormFor(domainId, patch) {
    setDestForms((prev) => ({
      ...prev,
      [domainId]: { ...destFormFor(domainId), ...patch }
    }));
  }

  function destEditKey(domainId, destinationId) {
    return `${domainId}-${destinationId}`;
  }

  function destEditDraftFor(domainId, dest) {
    const key = destEditKey(domainId, dest.id);
    if (destEditDrafts[key]) return destEditDrafts[key];
    return {
      label: dest.label || "",
      host: dest.host,
      port: String(dest.port),
      imap_auth_host: dest.imap_auth_host || "",
      imap_auth_port: dest.imap_auth_port ? String(dest.imap_auth_port) : ""
    };
  }

  function setDestEditDraft(domainId, destinationId, patch) {
    const key = destEditKey(domainId, destinationId);
    const dest = (domains.find((d) => d.id === domainId)?.destinations || []).find(
      (d) => d.id === destinationId
    );
    if (!dest) return;
    setDestEditDrafts((prev) => ({
      ...prev,
      [key]: { ...destEditDraftFor(domainId, dest), ...patch }
    }));
  }

  function cancelEditDestination(domainId, destinationId) {
    const key = destEditKey(domainId, destinationId);
    setEditingDestKey((current) => (current === key ? null : current));
    setDestEditDrafts((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }

  function startEditDestination(domainId, dest) {
    const key = destEditKey(domainId, dest.id);
    setEditingDestKey(key);
    setDestEditDrafts((prev) => ({
      ...prev,
      [key]: { label: dest.label || "", host: dest.host, port: String(dest.port) }
    }));
  }

  function destEditDirty(domainId, dest) {
    const key = destEditKey(domainId, dest.id);
    if (destEditDrafts[key] === undefined) return false;
    const draft = destEditDraftFor(domainId, dest);
    return (
      draft.label.trim() !== (dest.label || "").trim() ||
      draft.host.trim().toLowerCase() !== dest.host.toLowerCase() ||
      Number(draft.port) !== Number(dest.port) ||
      draft.imap_auth_host.trim().toLowerCase() !== (dest.imap_auth_host || "").trim().toLowerCase() ||
      (draft.imap_auth_port ? Number(draft.imap_auth_port) : null) !==
        (dest.imap_auth_port ?? null)
    );
  }

  async function saveDestination(event, domainId, destinationId) {
    event.preventDefault();
    const item = domains.find((d) => d.id === domainId);
    const dest = item?.destinations?.find((d) => d.id === destinationId);
    if (!dest) return;
    const draft = destEditDraftFor(domainId, dest);
    const key = destEditKey(domainId, destinationId);
    setDestBusy(key);
    try {
      const result = await api(`/domains/${domainId}/destinations/${destinationId}`, {
        method: "PUT",
        body: JSON.stringify({
          label: draft.label.trim(),
          host: draft.host.trim(),
          port: Number(draft.port) || 25,
          imap_auth_host: draft.imap_auth_host.trim() || null,
          imap_auth_port: draft.imap_auth_port ? Number(draft.imap_auth_port) : null
        })
      });
      onSyncWarning?.(result);
      cancelEditDestination(domainId, destinationId);
      await onRefresh?.();
    } catch (err) {
      alert(err.message);
    } finally {
      setDestBusy(null);
    }
  }

  function siblingDraftFor(item) {
    if (siblingDrafts[item.id] !== undefined) {
      return siblingDrafts[item.id];
    }
    return item.sibling_fqdn || "";
  }

  function setSiblingDraft(domainId, value) {
    setSiblingDrafts((prev) => ({ ...prev, [domainId]: value }));
  }

  function syncSecretDraftFor(item) {
    if (syncSecretDrafts[item.id] !== undefined) {
      return syncSecretDrafts[item.id];
    }
    return "";
  }

  function setSyncSecretDraft(domainId, value) {
    setSyncSecretDrafts((prev) => ({ ...prev, [domainId]: value }));
  }

  async function saveClusterConfig(domainId) {
    const item = domains.find((d) => d.id === domainId);
    if (!item) return;
    setSiblingBusy(domainId);
    try {
      const body = {
        sibling_fqdn: siblingDraftFor(item).trim() || null
      };
      if (syncSecretDrafts[item.id] !== undefined) {
        body.sync_secret = syncSecretDraftFor(item).trim() || null;
      }
      const result = await api(`/domains/${domainId}`, {
        method: "PUT",
        body: JSON.stringify(body)
      });
      onSyncWarning?.(result, { attemptSync: true });
      setSiblingDrafts((prev) => {
        const next = { ...prev };
        delete next[domainId];
        return next;
      });
      setSyncSecretDrafts((prev) => {
        const next = { ...prev };
        delete next[domainId];
        return next;
      });
      await onRefresh?.();
    } catch (err) {
      alert(err.message);
    } finally {
      setSiblingBusy(null);
    }
  }

  async function addDestination(event, domainId) {
    event.preventDefault();
    const form = destFormFor(domainId);
    setDestBusy(domainId);
    try {
      const result = await api(`/domains/${domainId}/destinations`, {
        method: "POST",
        body: JSON.stringify({
          label: form.label.trim(),
          host: form.host.trim(),
          port: Number(form.port) || 25,
          imap_auth_host: form.imap_auth_host.trim() || null,
          imap_auth_port: form.imap_auth_port ? Number(form.imap_auth_port) : null
        })
      });
      onSyncWarning?.(result);
      setDestFormFor(domainId, emptyDestForm);
      await onRefresh?.();
    } catch (err) {
      alert(err.message);
    } finally {
      setDestBusy(null);
    }
  }

  async function saveRelayAllInbound(domainId, enabled) {
    setRelayBusy(domainId);
    try {
      const result = await api(`/domains/${domainId}`, {
        method: "PUT",
        body: JSON.stringify({ relay_all_inbound: enabled })
      });
      onSyncWarning?.(result);
      await onRefresh?.();
    } catch (err) {
      alert(err.message);
    } finally {
      setRelayBusy(null);
    }
  }

  function relaySourceDraftFor(item) {
    if (relaySourceDrafts[item.id] !== undefined) {
      return relaySourceDrafts[item.id];
    }
    return (item.relay_source_ips || []).join("\n");
  }

  function setRelaySourceDraft(domainId, value) {
    setRelaySourceDrafts((prev) => ({ ...prev, [domainId]: value }));
  }

  async function saveRelaySourceIps(domainId) {
    const item = domains.find((d) => d.id === domainId);
    if (!item) return;
    setRelaySourceBusy(domainId);
    try {
      const lines = relaySourceDraftFor(item)
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);
      const result = await api(`/domains/${domainId}`, {
        method: "PUT",
        body: JSON.stringify({ relay_source_ips: lines })
      });
      onSyncWarning?.(result);
      setRelaySourceDrafts((prev) => {
        const next = { ...prev };
        delete next[domainId];
        return next;
      });
      await onRefresh?.();
    } catch (err) {
      alert(err.message);
    } finally {
      setRelaySourceBusy(null);
    }
  }

  async function handleAddDomainSubmit(event) {
    event.preventDefault();
    setAddDomainBusy(true);
    try {
      const newId = await onAddDomain(event);
      if (newId != null) {
        setAddDomainOpen(false);
        setActiveTabId(newId);
      }
    } finally {
      setAddDomainBusy(false);
    }
  }

  async function removeDestination(domainId, destinationId) {
    if (!confirm("Rimuovere questo server di destinazione?")) return;
    setDestBusy(`${domainId}-${destinationId}`);
    try {
      const result = await api(`/domains/${domainId}/destinations/${destinationId}`, { method: "DELETE" });
      onSyncWarning?.(result);
      await onRefresh?.();
    } catch (err) {
      alert(err.message);
    } finally {
      setDestBusy(null);
    }
  }

  return (
    <>
      <header className="page-header page-header-row">
        <div>
          <h2>Domini</h2>
          <p>
            Gestisci domini di posta, selector DKIM, server SMTP di destinazione e caselle email per il
            routing. Le caselle si configurano nel sotto-tab <strong>Caselle</strong> di ogni dominio.
          </p>
        </div>
        <div className="page-header-actions">
          <button
            type="button"
            className="btn-fab fab-add-domain"
            onClick={() => setAddDomainOpen(true)}
            aria-label="Aggiungi dominio"
            title="Aggiungi dominio"
          >
            +
          </button>
        </div>
      </header>

      <AddDomainModal
        open={addDomainOpen}
        onClose={() => {
          if (!addDomainBusy) setAddDomainOpen(false);
        }}
        domainForm={domainForm}
        setDomainForm={setDomainForm}
        busy={addDomainBusy}
        onSubmit={handleAddDomainSubmit}
      />

      <div className="panel">
        <h3>Domini configurati</h3>
        {domains.length === 0 ? (
          <p className="empty-state">Nessun dominio configurato.</p>
        ) : (
          <>
            <nav className="domain-tabs" aria-label="Domini configurati">
              {domains.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`domain-tab${activeTabId === item.id ? " active" : ""}`}
                  onClick={() => setActiveTabId(item.id)}
                  aria-current={activeTabId === item.id ? "true" : undefined}
                >
                  <span className="domain-tab__label">{item.name}</span>
                  {(!item.enabled || item.sibling_fqdn) && (
                    <span className="domain-tab__badges" aria-hidden="true">
                      {!item.enabled && (
                        <span className="domain-tab__badge domain-tab__badge--disabled">Off</span>
                      )}
                      {item.sibling_fqdn && (
                        <span className="domain-tab__badge domain-tab__badge--sync">Cluster</span>
                      )}
                      {item.relay_all_inbound ? (
                        <span className="domain-tab__badge domain-tab__badge--relay">Relay</span>
                      ) : null}
                      {(item.relay_source_ips || []).length > 0 ? (
                        <span className="domain-tab__badge domain-tab__badge--relay-ip">IP relay</span>
                      ) : null}
                    </span>
                  )}
                </button>
              ))}
            </nav>
            {domains.map((item) => {
              if (item.id !== activeTabId) return null;
              const destinations = item.destinations || [];
              const busy = destBusy === item.id;
              const siblingBusyItem = siblingBusy === item.id;
              const siblingDraft = siblingDraftFor(item);
              const syncSecretDraft = syncSecretDraftFor(item);
              const siblingDirty =
                siblingDrafts[item.id] !== undefined &&
                siblingDraft.trim() !== (item.sibling_fqdn || "");
              const syncSecretDirty = syncSecretDrafts[item.id] !== undefined;
              const clusterDirty = siblingDirty || syncSecretDirty;
              const relayAllInbound = !!item.relay_all_inbound;
              const relayBusyItem = relayBusy === item.id;
              const relaySourceDraft = relaySourceDraftFor(item);
              const relaySourceBusyItem = relaySourceBusy === item.id;
              const relaySourceDirty =
                relaySourceDrafts[item.id] !== undefined &&
                relaySourceDraft.trim() !== (item.relay_source_ips || []).join("\n");
              const settingsTab = settingsTabFor(item.id);
              return (
                <div key={item.id} className="domain-tab-panel">
                  <nav className="domain-settings-tabs" aria-label={`Impostazioni ${item.name}`}>
                    {SETTINGS_TABS.map((tab) => (
                      <button
                        key={tab.id}
                        type="button"
                        className={`domain-settings-tab${settingsTab === tab.id ? " active" : ""}`}
                        onClick={() => setSettingsTab(item.id, tab.id)}
                        aria-current={settingsTab === tab.id ? "true" : undefined}
                      >
                        {tab.label}
                      </button>
                    ))}
                  </nav>

                  {settingsTab === "generale" && (
                    <div className="domain-settings-panel">
                      <div className="list-item-actions-wrap">
                        <div className="list-item-meta">
                          <strong>{item.name}</strong>
                          <span>
                            selector <code>{item.dkim_selector}</code> · {item.mailbox_count} caselle ·{" "}
                            {item.enabled ? "abilitato" : "disabilitato"}
                            {item.sibling_fqdn ? (
                              <>
                                {" "}
                                · cluster → <code>{item.sibling_fqdn}</code>
                              </>
                            ) : null}
                          </span>
                        </div>
                        <div className="list-item-actions">
                          <button type="button" className="btn-secondary btn-sm" onClick={() => onToggleDomain(item)}>
                            {item.enabled ? "Disabilita" : "Abilita"}
                          </button>
                          <button type="button" className="btn-danger btn-sm" onClick={() => onDeleteDomain(item.id)}>
                            Elimina
                          </button>
                        </div>
                      </div>

                      <div className="domain-relay-all">
                        <CheckboxField
                          id={`relay-all-${item.id}`}
                          label="Inoltra tutta la posta in ingresso al server destinazione"
                          hint={
                            relayAllInbound
                              ? "Tutta la posta in ingresso per @dominio viene instradata all'unico server di destinazione, senza caselle esplicite."
                              : destinations.length === 1
                                ? "Abilita questa opzione per instradare qualsiasi indirizzo @dominio al server destinazione, senza caselle esplicite."
                                : destinations.length === 0
                                  ? "Aggiungi esattamente un server di destinazione e abilita il dominio per attivare l'inoltro catch-all."
                                  : "Rimuovi i server extra: con l'inoltro catch-all è consentito un solo server destinazione."
                          }
                          checked={relayAllInbound}
                          disabled={
                            relayBusyItem ||
                            (!relayAllInbound &&
                              (destinations.length !== 1 || !item.enabled))
                          }
                          onChange={(checked) => saveRelayAllInbound(item.id, checked)}
                        />
                        {relayAllInbound && destinations.length === 0 ? (
                          <p className="panel-hint panel-hint--warn" role="alert">
                            Inoltro catch-all attivo ma nessun server destinazione: la posta in ingresso verrà
                            rifiutata finché non configuri almeno un server.
                          </p>
                        ) : null}
                      </div>
                    </div>
                  )}

                  {settingsTab === "cluster" && (
                    <div className="domain-settings-panel domain-sibling">
                      <h4 className="domain-destinations__title">Server Cluster</h4>
                      <p className="panel-hint">
                        Configurazione cluster <strong>per questo dominio</strong>: ogni server può avere
                        un insieme di domini diverso; la replica riguarda solo il dominio selezionato.
                        FQDN dell&apos;altro nodo che riceve il bundle (caselle, selector/chiavi DKIM,
                        suggerimenti MX) al salvataggio, ad ogni modifica caselle e quando cambiano selector
                        o chiavi DKIM. Impostazioni locali come destinazioni e relay restano sul nodo.
                        Lasciare vuoto per disabilitare la replica. Sul peer, creare lo stesso dominio con
                        la stessa chiave precondivisa prima del primo push.
                      </p>
                      <form
                        className="form-grid form-grid--inline form-grid--inline-dest"
                        onSubmit={(e) => {
                          e.preventDefault();
                          saveClusterConfig(item.id);
                        }}
                      >
                        <FormField label="FQDN Server Cluster" htmlFor={`sibling-${item.id}`}>
                          <input
                            id={`sibling-${item.id}`}
                            placeholder="mx2.example.com"
                            value={siblingDraft}
                            onChange={(e) => setSiblingDraft(item.id, e.target.value)}
                            disabled={siblingBusyItem}
                          />
                        </FormField>
                        <FormField
                          label="Chiave precondivisa sync"
                          htmlFor={`sync-secret-${item.id}`}
                          hint={
                            item.sync_secret_configured && syncSecretDrafts[item.id] === undefined
                              ? "Chiave già configurata — lascia vuoto per mantenerla, o inserisci un nuovo valore per sostituirla"
                              : "Stesso valore su entrambi i nodi per questo dominio"
                          }
                        >
                          <input
                            id={`sync-secret-${item.id}`}
                            type="password"
                            autoComplete="new-password"
                            placeholder={
                              item.sync_secret_configured ? "•••••••• (configurata)" : "Segreto condiviso"
                            }
                            value={syncSecretDraft}
                            onChange={(e) => setSyncSecretDraft(item.id, e.target.value)}
                            disabled={siblingBusyItem}
                          />
                        </FormField>
                        <div className="form-actions">
                          <button
                            type="submit"
                            className="btn-secondary btn-sm"
                            disabled={siblingBusyItem || !clusterDirty}
                          >
                            {siblingBusyItem ? "Salvataggio..." : "Salva configurazione cluster"}
                          </button>
                        </div>
                      </form>
                    </div>
                  )}

                  {settingsTab === "destinazioni" && (
                    <div className="domain-settings-panel domain-destinations">
                      <h4 className="domain-destinations__title">Server di destinazione</h4>
                      <p className="panel-hint">
                        {relayAllInbound
                          ? "Con l'inoltro catch-all attivo è consentito un solo server di destinazione."
                          : "Elenco usato nel menu a tendina quando configuri le caselle di questo dominio."}
                      </p>
                      {relayAllInbound && destinations.length > 1 ? (
                        <p className="panel-hint panel-hint--warn" role="alert">
                          Sono configurati più server destinazione: rimuovi quelli in eccesso per
                          rispettare il vincolo dell&apos;inoltro catch-all.
                        </p>
                      ) : null}
                      {destinations.length === 0 ? (
                        <p className="empty-state">Nessun server configurato.</p>
                      ) : (
                        <ul className="list-items list-items--compact">
                          {destinations.map((dest) => {
                            const editKey = destEditKey(item.id, dest.id);
                            const isEditing = editingDestKey === editKey;
                            const editBusy = destBusy === editKey;
                            const editDraft = destEditDraftFor(item.id, dest);
                            const editDirty = destEditDirty(item.id, dest);
                            return (
                              <li key={dest.id} className="list-item">
                                {isEditing ? (
                                  <form
                                    className="form-grid form-grid--inline form-grid--inline-dest domain-dest-edit-form"
                                    onSubmit={(e) => saveDestination(e, item.id, dest.id)}
                                  >
                                    <FormField
                                      label="Etichetta"
                                      htmlFor={`dest-edit-label-${dest.id}`}
                                      hint="Opzionale"
                                      hintAfter
                                    >
                                      <input
                                        id={`dest-edit-label-${dest.id}`}
                                        placeholder="es. Backend principale"
                                        value={editDraft.label}
                                        onChange={(e) =>
                                          setDestEditDraft(item.id, dest.id, { label: e.target.value })
                                        }
                                        disabled={editBusy}
                                      />
                                    </FormField>
                                    <FormField label="Host" htmlFor={`dest-edit-host-${dest.id}`}>
                                      <input
                                        id={`dest-edit-host-${dest.id}`}
                                        placeholder="mail.backend.example"
                                        value={editDraft.host}
                                        onChange={(e) =>
                                          setDestEditDraft(item.id, dest.id, { host: e.target.value })
                                        }
                                        required
                                        disabled={editBusy}
                                      />
                                    </FormField>
                                    <FormField label="Porta" htmlFor={`dest-edit-port-${dest.id}`}>
                                      <input
                                        id={`dest-edit-port-${dest.id}`}
                                        type="number"
                                        min={1}
                                        max={65535}
                                        placeholder="25"
                                        value={editDraft.port}
                                        onChange={(e) =>
                                          setDestEditDraft(item.id, dest.id, { port: e.target.value })
                                        }
                                        disabled={editBusy}
                                      />
                                    </FormField>
                                    <FormField
                                      label="IMAP auth host"
                                      htmlFor={`dest-edit-imap-host-${dest.id}`}
                                      hint="Default: host destinazione"
                                      hintAfter
                                    >
                                      <input
                                        id={`dest-edit-imap-host-${dest.id}`}
                                        placeholder="mdaemon.example.com"
                                        value={editDraft.imap_auth_host}
                                        onChange={(e) =>
                                          setDestEditDraft(item.id, dest.id, {
                                            imap_auth_host: e.target.value
                                          })
                                        }
                                        disabled={editBusy}
                                      />
                                    </FormField>
                                    <FormField
                                      label="IMAP auth porta"
                                      htmlFor={`dest-edit-imap-port-${dest.id}`}
                                      hint="Default: 993"
                                      hintAfter
                                    >
                                      <input
                                        id={`dest-edit-imap-port-${dest.id}`}
                                        type="number"
                                        min={1}
                                        max={65535}
                                        placeholder="993"
                                        value={editDraft.imap_auth_port}
                                        onChange={(e) =>
                                          setDestEditDraft(item.id, dest.id, {
                                            imap_auth_port: e.target.value
                                          })
                                        }
                                        disabled={editBusy}
                                      />
                                    </FormField>
                                    <div className="form-actions">
                                      <button
                                        type="submit"
                                        className="btn-secondary btn-sm"
                                        disabled={editBusy || !editDirty}
                                      >
                                        {editBusy ? "Salvataggio..." : "Salva"}
                                      </button>
                                      <button
                                        type="button"
                                        className="btn-secondary btn-sm"
                                        disabled={editBusy}
                                        onClick={() => cancelEditDestination(item.id, dest.id)}
                                      >
                                        Annulla
                                      </button>
                                    </div>
                                  </form>
                                ) : (
                                  <>
                                    <div className="list-item-meta">
                                      <strong>{destinationLabel(dest)}</strong>
                                    </div>
                                    <div className="list-item-actions">
                                      <button
                                        type="button"
                                        className="btn-secondary btn-sm"
                                        disabled={destBusy !== null}
                                        onClick={() => startEditDestination(item.id, dest)}
                                      >
                                        Modifica
                                      </button>
                                      <button
                                        type="button"
                                        className="btn-danger btn-sm"
                                        disabled={destBusy !== null}
                                        onClick={() => removeDestination(item.id, dest.id)}
                                      >
                                        Rimuovi
                                      </button>
                                    </div>
                                  </>
                                )}
                              </li>
                            );
                          })}
                        </ul>
                      )}
                      {!(relayAllInbound && destinations.length >= 1) ? (
                      <form
                        onSubmit={(e) => addDestination(e, item.id)}
                        className="form-grid form-grid--inline form-grid--inline-dest"
                      >
                        <FormField
                          label="Etichetta"
                          htmlFor={`dest-label-${item.id}`}
                          hint="Opzionale"
                          hintAfter
                        >
                          <input
                            id={`dest-label-${item.id}`}
                            placeholder="es. Backend principale"
                            value={destFormFor(item.id).label}
                            onChange={(e) => setDestFormFor(item.id, { label: e.target.value })}
                            disabled={busy}
                          />
                        </FormField>
                        <FormField label="Host" htmlFor={`dest-host-${item.id}`}>
                          <input
                            id={`dest-host-${item.id}`}
                            placeholder="mail.backend.example"
                            value={destFormFor(item.id).host}
                            onChange={(e) => setDestFormFor(item.id, { host: e.target.value })}
                            required
                            disabled={busy}
                          />
                        </FormField>
                        <FormField label="Porta" htmlFor={`dest-port-${item.id}`}>
                          <input
                            id={`dest-port-${item.id}`}
                            type="number"
                            placeholder="25"
                            value={destFormFor(item.id).port}
                            onChange={(e) => setDestFormFor(item.id, { port: e.target.value })}
                            disabled={busy}
                          />
                        </FormField>
                        <FormField
                          label="IMAP auth host"
                          htmlFor={`dest-imap-host-${item.id}`}
                          hint="Default: host destinazione"
                          hintAfter
                        >
                          <input
                            id={`dest-imap-host-${item.id}`}
                            placeholder="mdaemon.example.com"
                            value={destFormFor(item.id).imap_auth_host}
                            onChange={(e) =>
                              setDestFormFor(item.id, { imap_auth_host: e.target.value })
                            }
                            disabled={busy}
                          />
                        </FormField>
                        <FormField
                          label="IMAP auth porta"
                          htmlFor={`dest-imap-port-${item.id}`}
                          hint="Default: 993 (SSL) o 143 (STARTTLS)"
                          hintAfter
                        >
                          <input
                            id={`dest-imap-port-${item.id}`}
                            type="number"
                            placeholder="993"
                            value={destFormFor(item.id).imap_auth_port}
                            onChange={(e) =>
                              setDestFormFor(item.id, { imap_auth_port: e.target.value })
                            }
                            disabled={busy}
                          />
                        </FormField>
                        <div className="form-actions">
                          <button type="submit" className="btn-secondary btn-sm" disabled={busy}>
                            {busy ? "Aggiunta..." : "Aggiungi server"}
                          </button>
                        </div>
                      </form>
                      ) : null}
                    </div>
                  )}

                  {settingsTab === "caselle" && (
                    <DomainMailboxesPanel
                      domain={item}
                      domains={domains}
                      enabledDomains={enabledDomains}
                      mailboxes={mailboxes}
                      mailboxForm={mailboxForm}
                      setMailboxForm={setMailboxForm}
                      onAddMailbox={onAddMailbox}
                      onUpdateMailbox={onUpdateMailbox}
                      onDeleteMailbox={onDeleteMailbox}
                      onImportCsv={onImportCsv}
                      importUpdateExisting={importUpdateExisting}
                      setImportUpdateExisting={setImportUpdateExisting}
                      importSkipHeader={importSkipHeader}
                      setImportSkipHeader={setImportSkipHeader}
                      importResult={importResult}
                      importBusy={importBusy}
                    />
                  )}

                  {settingsTab === "dns" && (
                    <DomainDnsSettingsPanel domainName={item.name} active={settingsTab === "dns"} />
                  )}

                  {settingsTab === "relay" && (
                    <div className="domain-settings-panel domain-relay-sources">
                      <h4 className="domain-destinations__title">Relay in uscita</h4>
                      <p className="panel-hint">
                        IP sorgenti relay consentite: un indirizzo IP o blocco CIDR per riga. Consente
                        l&apos;invio in uscita (relay) solo se il client si connette da uno di questi IP e
                        dichiara un mittente @{item.name}, oppure se autenticato via SASL sulla porta 587.
                        La posta in ingresso da Internet (MX) non è influenzata.
                      </p>
                      <form
                        className="form-grid"
                        onSubmit={(e) => {
                          e.preventDefault();
                          saveRelaySourceIps(item.id);
                        }}
                      >
                        <FormField
                          label="IP sorgenti relay consentite"
                          htmlFor={`relay-sources-${item.id}`}
                          hint="Es. 203.0.113.10 o 192.168.1.0/24 — una voce per riga, max 64."
                          hintAfter
                        >
                          <textarea
                            id={`relay-sources-${item.id}`}
                            rows={4}
                            placeholder={"203.0.113.10\n192.168.1.0/24"}
                            value={relaySourceDraft}
                            onChange={(e) => setRelaySourceDraft(item.id, e.target.value)}
                            disabled={relaySourceBusyItem}
                          />
                        </FormField>
                        <div className="form-actions">
                          <button
                            type="submit"
                            className="btn-secondary btn-sm"
                            disabled={relaySourceBusyItem || !relaySourceDirty}
                          >
                            {relaySourceBusyItem ? "Salvataggio..." : "Salva IP relay"}
                          </button>
                        </div>
                      </form>
                    </div>
                  )}
                </div>
              );
            })}
          </>
        )}
      </div>
    </>
  );
}
