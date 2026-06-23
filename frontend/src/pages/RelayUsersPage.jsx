import { FormField } from "../components/FormField";

export default function RelayUsersPage({
  relayUsers,
  relayUserForm,
  setRelayUserForm,
  onAddRelayUser,
  onUpdateRelayUser,
  onDeleteRelayUser
}) {
  return (
    <>
      <header className="page-header">
        <h2>Utenti relay SMTP</h2>
        <p>
          Account per l&apos;invio autenticato da client mobili o remoto sulla porta{" "}
          <strong>587 (submission)</strong>. I relay da IP fidati (mynetworks / relay per dominio)
          restano disponibili senza autenticazione.
        </p>
      </header>

      <div className="panel">
        <h3>Aggiungi utente relay</h3>
        <form
          onSubmit={onAddRelayUser}
          className="form-grid form-grid--inline form-grid--inline-users"
        >
          <FormField label="Nome utente" htmlFor="relay-username">
            <input
              id="relay-username"
              placeholder="mobile1"
              value={relayUserForm.username}
              onChange={(e) =>
                setRelayUserForm({ ...relayUserForm, username: e.target.value })
              }
              required
              autoComplete="off"
            />
          </FormField>
          <FormField
            label="Password"
            htmlFor="relay-password"
            hint="Minimo 8 caratteri"
            hintAfter
          >
            <input
              id="relay-password"
              type="password"
              placeholder="••••••••"
              value={relayUserForm.password}
              onChange={(e) =>
                setRelayUserForm({ ...relayUserForm, password: e.target.value })
              }
              required
              minLength={8}
              autoComplete="new-password"
            />
          </FormField>
          <FormField label="Attivo" htmlFor="relay-enabled">
            <select
              id="relay-enabled"
              value={relayUserForm.enabled ? "1" : "0"}
              onChange={(e) =>
                setRelayUserForm({
                  ...relayUserForm,
                  enabled: e.target.value === "1"
                })
              }
            >
              <option value="1">Sì</option>
              <option value="0">No</option>
            </select>
          </FormField>
          <div className="form-actions">
            <button type="submit" className="btn-primary">
              Crea utente relay
            </button>
          </div>
        </form>
      </div>

      <div className="panel">
        <h3>Utenti relay esistenti</h3>
        {relayUsers.length === 0 ? (
          <p className="empty-state">Nessun utente relay configurato.</p>
        ) : (
          <ul className="list-items">
            {relayUsers.map((item) => (
              <li key={item.id} className="list-item list-item-stack">
                <div className="list-item-meta">
                  <strong>{item.username}</strong>
                  <span>
                    {item.enabled ? "attivo" : "disabilitato"}
                    {item.created_at ? ` · creato ${item.created_at}` : ""}
                  </span>
                </div>
                <div className="list-item-actions list-item-actions-wrap">
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() =>
                      onUpdateRelayUser(item.id, { enabled: !item.enabled })
                    }
                  >
                    {item.enabled ? "Disabilita" : "Abilita"}
                  </button>
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => {
                      const pwd = prompt(
                        `Nuova password per ${item.username} (min. 8 caratteri):`
                      );
                      if (pwd && pwd.length >= 8) {
                        onUpdateRelayUser(item.id, { password: pwd });
                      } else if (pwd) {
                        alert("Password troppo corta.");
                      }
                    }}
                  >
                    Reimposta password
                  </button>
                  <button
                    type="button"
                    className="btn-danger btn-sm"
                    onClick={() => onDeleteRelayUser(item.id, item.username)}
                  >
                    Elimina
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
