import { useCallback, useEffect, useState } from "react";
import { api, apiUpload } from "./api";
import Layout from "./components/Layout";
import { useTheme } from "./hooks/useTheme";
import DomainsPage from "./pages/DomainsPage";
import SpamPage from "./pages/SpamPage";
import QuarantinePage from "./pages/QuarantinePage";
import MfaPage from "./pages/MfaPage";
import ProfilePage from "./pages/ProfilePage";
import SettingsPage from "./pages/SettingsPage";
import SystemPage from "./pages/SystemPage";
import TrafficPage from "./pages/TrafficPage";
import UsersPage from "./pages/UsersPage";

export default function Dashboard({ user, onLogout, onUserUpdate }) {
  const { theme, toggleTheme } = useTheme();
  const [activePage, setActivePage] = useState("domains");
  const [openSettingsTab, setOpenSettingsTab] = useState(() => {
    const tab = sessionStorage.getItem("mx-open-tab");
    if (tab) sessionStorage.removeItem("mx-open-tab");
    return tab;
  });
  const [domains, setDomains] = useState([]);
  const [mailboxes, setMailboxes] = useState([]);
  const [domainForm, setDomainForm] = useState({ name: "", dkim_selector: "mail", sibling_fqdn: "" });
  const [syncWarning, setSyncWarning] = useState("");
  const [mailboxForm, setMailboxForm] = useState({
    local_part: "",
    domain_id: "",
    destination_id: ""
  });
  const [filterDomainId, setFilterDomainId] = useState("");
  const [importUpdateExisting, setImportUpdateExisting] = useState(false);
  const [importSkipHeader, setImportSkipHeader] = useState(false);
  const [importResult, setImportResult] = useState(null);
  const [importBusy, setImportBusy] = useState(false);
  const [users, setUsers] = useState([]);
  const [userForm, setUserForm] = useState({ username: "", password: "", role: "user", notify_email: "" });
  const [error, setError] = useState("");

  const isAdmin = user.role === "admin";

  const enabledDomains = domains.filter((d) => d.enabled);
  const selectedDomain =
    domains.find((d) => String(d.id) === String(mailboxForm.domain_id)) || enabledDomains[0];

  const handleAuthError = useCallback(
    (err) => {
      if (err.unauthorized) {
        onLogout();
      } else {
        setError(err.message);
      }
    },
    [onLogout]
  );

  const noteSyncWarning = useCallback((result, { attemptSync } = {}) => {
    if (result?.sync_warning) {
      setSyncWarning(result.sync_warning);
    } else if (attemptSync) {
      setSyncWarning("");
    }
  }, []);

  const handleDomainTabChange = useCallback(
    (domainId) => {
      setFilterDomainId(String(domainId));
      setMailboxForm((prev) => {
        const domain = domains.find((d) => d.id === domainId);
        const firstDest = domain?.destinations?.[0];
        const sameDomain = String(prev.domain_id) === String(domainId);
        return {
          ...prev,
          local_part: sameDomain ? prev.local_part : "",
          domain_id: String(domainId),
          destination_id: firstDest ? String(firstDest.id) : ""
        };
      });
    },
    [domains]
  );

  const loadUsers = useCallback(async () => {
    if (!isAdmin) return;
    try {
      setUsers(await api("/users"));
    } catch (err) {
      handleAuthError(err);
    }
  }, [isAdmin, handleAuthError]);

  async function load() {
    setError("");
    try {
      const domainQuery = filterDomainId ? `?domain_id=${filterDomainId}` : "";
      const requests = [
        api("/domains"),
        api(`/mailboxes${domainQuery}`),
      ];
      if (isAdmin) {
        requests.push(api("/users"));
      }
      const results = await Promise.all(requests);
      const d = results[0];
      const m = results[1];
      setDomains(d);
      setMailboxes(m);
      if (isAdmin) {
        setUsers(results[2]);
      }
      if (!mailboxForm.domain_id && d.length > 0) {
        const first = d.find((x) => x.enabled) || d[0];
        const firstDest = first.destinations?.[0];
        setMailboxForm((prev) => ({
          ...prev,
          domain_id: String(first.id),
          destination_id: firstDest ? String(firstDest.id) : ""
        }));
      }
    } catch (err) {
      handleAuthError(err);
    }
  }

  useEffect(() => {
    load();
  }, [filterDomainId, isAdmin]);

  useEffect(() => {
    if (activePage === "users" && isAdmin) {
      loadUsers();
    }
  }, [activePage, isAdmin, loadUsers]);

  async function addDomain(event) {
    event.preventDefault();
    setError("");
    setSyncWarning("");
    try {
      const siblingFqdn = domainForm.sibling_fqdn.trim();
      const result = await api("/domains", {
        method: "POST",
        body: JSON.stringify({
          name: domainForm.name,
          enabled: true,
          dkim_selector: domainForm.dkim_selector || "mail",
          sibling_fqdn: siblingFqdn || null
        })
      });
      setDomainForm({ name: "", dkim_selector: "mail", sibling_fqdn: "" });
      noteSyncWarning(result, { attemptSync: !!siblingFqdn });
      await load();
      return result.id;
    } catch (err) {
      handleAuthError(err);
    }
  }

  async function toggleDomain(domain) {
    setError("");
    setSyncWarning("");
    try {
      const result = await api(`/domains/${domain.id}`, {
        method: "PUT",
        body: JSON.stringify({ enabled: !domain.enabled })
      });
      noteSyncWarning(result);
      await load();
    } catch (err) {
      handleAuthError(err);
    }
  }

  async function deleteDomain(id) {
    if (!confirm("Eliminare questo dominio?")) return;
    setError("");
    setSyncWarning("");
    try {
      const result = await api(`/domains/${id}`, { method: "DELETE" });
      noteSyncWarning(result);
      await load();
    } catch (err) {
      handleAuthError(err);
    }
  }

  async function addMailbox(event) {
    event.preventDefault();
    if (!selectedDomain) {
      setError("Aggiungi almeno un dominio abilitato prima di creare caselle.");
      return;
    }
    const destination = (selectedDomain.destinations || []).find(
      (d) => String(d.id) === String(mailboxForm.destination_id)
    );
    if (!destination) {
      setError("Seleziona un server di destinazione configurato per il dominio (pagina Domini).");
      return;
    }
    const email = `${mailboxForm.local_part.trim()}@${selectedDomain.name}`;
    setError("");
    setSyncWarning("");
    try {
      const result = await api("/mailboxes", {
        method: "POST",
        body: JSON.stringify({
          email,
          domain_id: Number(selectedDomain.id),
          destination_host: destination.host,
          destination_port: Number(destination.port),
          enabled: true
        })
      });
      noteSyncWarning(result);
      const firstDest = selectedDomain.destinations?.[0];
      setMailboxForm({
        local_part: "",
        domain_id: String(selectedDomain.id),
        destination_id: firstDest ? String(firstDest.id) : ""
      });
      await load();
    } catch (err) {
      handleAuthError(err);
    }
  }

  async function importMailboxesCsv(file) {
    setError("");
    setSyncWarning("");
    setImportBusy(true);
    setImportResult(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      if (importUpdateExisting) {
        formData.append("update_existing", "true");
      }
      if (importSkipHeader) {
        formData.append("skip_header", "true");
      }
      const result = await apiUpload("/mailboxes/import", formData);
      setImportResult(result);
      noteSyncWarning(result);
      await load();
    } catch (err) {
      handleAuthError(err);
    } finally {
      setImportBusy(false);
    }
  }

  async function updateMailbox(id, patch) {
    setError("");
    setSyncWarning("");
    try {
      const result = await api(`/mailboxes/${id}`, {
        method: "PUT",
        body: JSON.stringify(patch)
      });
      noteSyncWarning(result);
      await load();
    } catch (err) {
      handleAuthError(err);
      throw err;
    }
  }

  async function deleteMailbox(id) {
    setError("");
    setSyncWarning("");
    try {
      const result = await api(`/mailboxes/${id}`, { method: "DELETE" });
      noteSyncWarning(result);
      await load();
    } catch (err) {
      handleAuthError(err);
    }
  }

  async function addUser(event) {
    event.preventDefault();
    setError("");
    try {
      await api("/users", {
        method: "POST",
        body: JSON.stringify({
          username: userForm.username.trim(),
          password: userForm.password,
          role: userForm.role,
          notify_email: userForm.notify_email?.trim() || ""
        })
      });
      setUserForm({ username: "", password: "", role: "user", notify_email: "" });
      await loadUsers();
    } catch (err) {
      handleAuthError(err);
    }
  }

  async function updateAppUser(userId, patch) {
    setError("");
    try {
      await api(`/users/${userId}`, {
        method: "PUT",
        body: JSON.stringify(patch)
      });
      await loadUsers();
    } catch (err) {
      handleAuthError(err);
    }
  }

  async function deleteAppUser(userId, username) {
    if (!confirm(`Eliminare l'utente ${username}?`)) return;
    setError("");
    try {
      await api(`/users/${userId}`, { method: "DELETE" });
      await loadUsers();
    } catch (err) {
      handleAuthError(err);
    }
  }

  function handleLogout() {
    api("/auth/logout", { method: "POST" }).catch(() => {});
    onLogout();
  }

  async function handleUserUpdate() {
    try {
      const me = await api("/auth/me");
      onUserUpdate(me);
    } catch (err) {
      handleAuthError(err);
    }
  }

  function renderPage() {
    switch (activePage) {
      case "domains":
        return (
          <DomainsPage
            domains={domains}
            domainForm={domainForm}
            setDomainForm={setDomainForm}
            onAddDomain={addDomain}
            onToggleDomain={toggleDomain}
            onDeleteDomain={deleteDomain}
            onRefresh={load}
            onSyncWarning={noteSyncWarning}
            onDomainTabChange={handleDomainTabChange}
            openSettingsTab={openSettingsTab}
            onOpenSettingsTabConsumed={() => setOpenSettingsTab(null)}
            enabledDomains={enabledDomains}
            mailboxes={mailboxes}
            mailboxForm={mailboxForm}
            setMailboxForm={setMailboxForm}
            onAddMailbox={addMailbox}
            onUpdateMailbox={updateMailbox}
            onDeleteMailbox={deleteMailbox}
            onImportCsv={importMailboxesCsv}
            importUpdateExisting={importUpdateExisting}
            setImportUpdateExisting={setImportUpdateExisting}
            importSkipHeader={importSkipHeader}
            setImportSkipHeader={setImportSkipHeader}
            importResult={importResult}
            importBusy={importBusy}
          />
        );
      case "settings":
        if (!isAdmin) {
          return (
            <p className="empty-state">Solo gli amministratori possono modificare le impostazioni.</p>
          );
        }
        return (
          <SettingsPage
            isAdmin={isAdmin}
            onError={(msg) => (msg ? setError(msg) : setError(""))}
          />
        );
      case "spam":
        return <SpamPage onError={(msg) => (msg ? setError(msg) : setError(""))} />;
      case "quarantine":
        if (!isAdmin) {
          return (
            <p className="empty-state">Solo gli amministratori possono gestire la quarantena.</p>
          );
        }
        return <QuarantinePage onError={(msg) => (msg ? setError(msg) : setError(""))} />;
      case "traffic":
        return <TrafficPage isAdmin={isAdmin} />;
      case "mfa":
        return <MfaPage user={user} onUserUpdate={handleUserUpdate} />;
      case "profile":
        return <ProfilePage user={user} onUserUpdate={handleUserUpdate} />;
      case "status":
        return <SystemPage user={user} onLogout={handleLogout} />;
      case "users":
        if (!isAdmin) {
          return (
            <p className="empty-state">Solo gli amministratori possono gestire gli utenti.</p>
          );
        }
        return (
          <UsersPage
            users={users}
            userForm={userForm}
            setUserForm={setUserForm}
            onAddUser={addUser}
            onUpdateUser={updateAppUser}
            onDeleteUser={deleteAppUser}
            currentUserId={user.id}
          />
        );
      default:
        return null;
    }
  }

  return (
    <Layout
      activePage={activePage}
      onNavigate={setActivePage}
      user={user}
      theme={theme}
      onToggleTheme={toggleTheme}
      onLogout={handleLogout}
    >
      {error && <div className="alert-error">{error}</div>}
      {syncWarning && (
        <div className="alert-warn" role="alert">
          Sync Server Cluster: {syncWarning}
        </div>
      )}
      {renderPage()}
    </Layout>
  );
}
