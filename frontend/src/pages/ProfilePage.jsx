import { useEffect, useState } from "react";
import { api } from "../api";
import { FormField } from "../components/FormField";
import MfaSetupPanel from "../components/MfaSetupPanel";

export default function ProfilePage({ user, onUserUpdate }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const [passwordSuccess, setPasswordSuccess] = useState("");
  const [passwordLoading, setPasswordLoading] = useState(false);
  const [notifyEmail, setNotifyEmail] = useState(user.notify_email || "");
  const [notifyError, setNotifyError] = useState("");
  const [notifySuccess, setNotifySuccess] = useState("");
  const [notifyLoading, setNotifyLoading] = useState(false);

  useEffect(() => {
    setNotifyEmail(user.notify_email || "");
  }, [user.notify_email]);

  async function handleChangePassword(event) {
    event.preventDefault();
    setPasswordError("");
    setPasswordSuccess("");
    if (newPassword !== confirmPassword) {
      setPasswordError("La nuova password e la conferma non coincidono.");
      return;
    }
    if (newPassword.length < 8) {
      setPasswordError("La nuova password deve avere almeno 8 caratteri.");
      return;
    }
    setPasswordLoading(true);
    try {
      await api("/auth/password", {
        method: "POST",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword
        })
      });
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setPasswordSuccess("Password aggiornata correttamente.");
    } catch (err) {
      setPasswordError(err.message);
    } finally {
      setPasswordLoading(false);
    }
  }

  return (
    <>
      <header className="page-header">
        <h2>Il mio account</h2>
        <p>Profilo, password e autenticazione a due fattori.</p>
      </header>

      <div className="panel">
        <h3>Profilo</h3>
        <ul className="list-items" style={{ marginTop: 0 }}>
          <li className="list-item" style={{ paddingTop: 0 }}>
            <div className="list-item-meta">
              <strong>{user.username}</strong>
              <span>
                Ruolo: {user.role === "admin" ? "Amministratore" : "Utente"}
                {user.mfa_enabled ? " · MFA attivo" : " · MFA non attivo"}
              </span>
            </div>
          </li>
        </ul>
        <p className="panel-hint" style={{ marginTop: "0.75rem" }}>
          Indica un indirizzo email per ricevere i report automatici degli errori rilevati su Postfix,
          Amavis e altri servizi dello stack (invio ogni ~15 minuti se ci sono nuovi eventi).
        </p>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            setNotifyError("");
            setNotifySuccess("");
            setNotifyLoading(true);
            try {
              const result = await api("/auth/profile/notify-email", {
                method: "PUT",
                body: JSON.stringify({ notify_email: notifyEmail.trim() })
              });
              setNotifySuccess("Email di notifica aggiornata.");
              onUserUpdate?.(result.user);
            } catch (err) {
              setNotifyError(err.message);
            } finally {
              setNotifyLoading(false);
            }
          }}
          className="form-grid"
        >
          <FormField label="Email per notifiche errori" htmlFor="profile-notify-email">
            <input
              id="profile-notify-email"
              type="email"
              placeholder="tu@azienda.it"
              value={notifyEmail}
              onChange={(e) => setNotifyEmail(e.target.value)}
            />
          </FormField>
          <button type="submit" className="btn-primary" disabled={notifyLoading}>
            {notifyLoading ? "Salvataggio..." : "Salva email notifiche"}
          </button>
        </form>
        {notifyError && <p className="auth-error form-field-error">{notifyError}</p>}
        {notifySuccess && <p className="mfa-success">{notifySuccess}</p>}
      </div>

      <div className="panel">
        <h3>Cambia password</h3>
        <p className="panel-hint">Inserisci la password attuale e scegli una nuova password (min. 8 caratteri).</p>
        <form onSubmit={handleChangePassword} className="form-grid">
          <FormField label="Password attuale" htmlFor="profile-current-password">
            <input
              id="profile-current-password"
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </FormField>
          <FormField label="Nuova password" htmlFor="profile-new-password">
            <input
              id="profile-new-password"
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
              minLength={8}
              required
            />
          </FormField>
          <FormField label="Conferma nuova password" htmlFor="profile-confirm-password">
            <input
              id="profile-confirm-password"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
              minLength={8}
              required
            />
          </FormField>
          <button type="submit" className="btn-primary" disabled={passwordLoading}>
            {passwordLoading ? "Salvataggio..." : "Aggiorna password"}
          </button>
        </form>
        {passwordError && <p className="auth-error form-field-error">{passwordError}</p>}
        {passwordSuccess && <p className="mfa-success">{passwordSuccess}</p>}
      </div>

      <MfaSetupPanel user={user} onUpdated={onUserUpdate} />
    </>
  );
}
