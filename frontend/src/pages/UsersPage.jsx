import { FormField } from "../components/FormField";

export default function UsersPage({
  users,
  userForm,
  setUserForm,
  onAddUser,
  onUpdateUser,
  onDeleteUser,
  currentUserId
}) {
  return (
    <>
      <header className="page-header">
        <h2>Utenti applicazione</h2>
        <p>
          Crea account con accesso al pannello. Solo gli <strong>admin</strong> possono gestire
          domini, caselle e questa sezione. Gli utenti <strong>user</strong> hanno accesso in
          lettura alle funzioni mail e sicurezza (senza configurazione utenti).
        </p>
      </header>

      <div className="panel">
        <h3>Aggiungi utente</h3>
        <form onSubmit={onAddUser} className="form-grid form-grid--inline form-grid--inline-users">
          <FormField label="Nome utente" htmlFor="user-username">
            <input
              id="user-username"
              placeholder="nome utente"
              value={userForm.username}
              onChange={(e) => setUserForm({ ...userForm, username: e.target.value })}
              required
              autoComplete="off"
            />
          </FormField>
          <FormField
            label="Password"
            htmlFor="user-password"
            hint="Minimo 8 caratteri"
            hintAfter
          >
            <input
              id="user-password"
              type="password"
              placeholder="••••••••"
              value={userForm.password}
              onChange={(e) => setUserForm({ ...userForm, password: e.target.value })}
              required
              minLength={8}
              autoComplete="new-password"
            />
          </FormField>
          <FormField label="Ruolo" htmlFor="user-role">
            <select
              id="user-role"
              value={userForm.role}
              onChange={(e) => setUserForm({ ...userForm, role: e.target.value })}
            >
              <option value="user">user</option>
              <option value="admin">admin</option>
            </select>
          </FormField>
          <FormField
            label="Email notifiche errori"
            htmlFor="user-notify-email"
            hint="Opzionale — report errori stack mail"
            hintAfter
          >
            <input
              id="user-notify-email"
              type="email"
              placeholder="utente@azienda.it"
              value={userForm.notify_email || ""}
              onChange={(e) => setUserForm({ ...userForm, notify_email: e.target.value })}
            />
          </FormField>
          <div className="form-actions">
            <button type="submit" className="btn-primary">
              Crea utente
            </button>
          </div>
        </form>
      </div>

      <div className="panel">
        <h3>Utenti esistenti</h3>
        {users.length === 0 ? (
          <p className="empty-state">Nessun utente oltre al bootstrap.</p>
        ) : (
          <ul className="list-items">
            {users.map((item) => (
              <li key={item.id} className="list-item list-item-stack">
                <div className="list-item-meta">
                  <strong>{item.username}</strong>
                  <span>
                    ruolo <code>{item.role}</code>
                    {item.mfa_enabled ? " · MFA attivo" : " · MFA non attivo"}
                    {item.notify_email ? ` · notifiche ${item.notify_email}` : " · nessuna email notifiche"}
                    {item.id === currentUserId ? " · tu" : ""}
                  </span>
                </div>
                <div className="list-item-actions list-item-actions-wrap">
                  {item.role !== "admin" ? (
                    <button
                      type="button"
                      className="btn-secondary btn-sm"
                      onClick={() => onUpdateUser(item.id, { role: "admin" })}
                    >
                      Promuovi admin
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn-secondary btn-sm"
                      onClick={() => onUpdateUser(item.id, { role: "user" })}
                      disabled={item.id === currentUserId}
                    >
                      Rendi user
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => {
                      const email = prompt(
                        `Email notifiche errori per ${item.username} (vuoto per disattivare):`,
                        item.notify_email || ""
                      );
                      if (email !== null) {
                        onUpdateUser(item.id, { notify_email: email.trim() });
                      }
                    }}
                  >
                    Email notifiche
                  </button>
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => {
                      const pwd = prompt(`Nuova password per ${item.username} (min. 8 caratteri):`);
                      if (pwd && pwd.length >= 8) {
                        onUpdateUser(item.id, { password: pwd });
                      } else if (pwd) {
                        alert("Password troppo corta.");
                      }
                    }}
                  >
                    Reimposta password
                  </button>
                  {item.mfa_enabled && (
                    <button
                      type="button"
                      className="btn-secondary btn-sm"
                      onClick={() => {
                        if (confirm(`Disattivare MFA per ${item.username}?`)) {
                          onUpdateUser(item.id, { reset_mfa: true });
                        }
                      }}
                    >
                      Reset MFA
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn-danger btn-sm"
                    onClick={() => onDeleteUser(item.id, item.username)}
                    disabled={item.id === currentUserId}
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
