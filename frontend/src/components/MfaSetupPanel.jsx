import { useState } from "react";
import { api } from "../api";
import { FormField } from "./FormField";

export default function MfaSetupPanel({ user, onUpdated }) {
  const [setup, setSetup] = useState(null);
  const [code, setCode] = useState("");
  const [disablePassword, setDisablePassword] = useState("");
  const [disableCode, setDisableCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function startSetup() {
    setError("");
    setLoading(true);
    try {
      const data = await api("/auth/mfa/setup", { method: "POST" });
      setSetup(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function confirmSetup(event) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      await api("/auth/mfa/confirm", {
        method: "POST",
        body: JSON.stringify({ code })
      });
      setSetup(null);
      setCode("");
      onUpdated();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function disableMfa(event) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      await api("/auth/mfa/disable", {
        method: "POST",
        body: JSON.stringify({ password: disablePassword, code: disableCode })
      });
      setDisablePassword("");
      setDisableCode("");
      onUpdated();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  if (user.mfa_enabled) {
    return (
      <div className="mfa-panel panel">
        <h3>Autenticazione a due fattori (MFA)</h3>
        <p className="mfa-success">MFA attivo per questo account.</p>
        <p className="panel-hint">
          Per disattivare MFA inserisci la password dell&apos;account e un codice dall&apos;app
          authenticator.
        </p>
        <form onSubmit={disableMfa} className="form-grid">
          <FormField label="Password" htmlFor="mfa-disable-password">
            <input
              id="mfa-disable-password"
              type="password"
              value={disablePassword}
              onChange={(e) => setDisablePassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </FormField>
          <FormField label="Codice authenticator" htmlFor="mfa-disable-code">
            <input
              id="mfa-disable-code"
              placeholder="123456"
              value={disableCode}
              onChange={(e) => setDisableCode(e.target.value.replace(/\D/g, "").slice(0, 8))}
              inputMode="numeric"
              autoComplete="one-time-code"
              required
            />
          </FormField>
          <button type="submit" className="btn-danger" disabled={loading}>
            {loading ? "Disattivazione..." : "Disattiva MFA"}
          </button>
        </form>
        {error && <p className="auth-error form-field-error">{error}</p>}
      </div>
    );
  }

  return (
    <div className="mfa-panel panel">
      <h3>Autenticazione a due fattori (MFA)</h3>
      <p className="panel-hint">
        Proteggi l&apos;accesso con un codice TOTP da app come Google Authenticator o Authy.
      </p>
      {!setup ? (
        <button type="button" className="btn-primary" onClick={startSetup} disabled={loading}>
          {loading ? "Generazione..." : "Configura MFA"}
        </button>
      ) : (
        <form onSubmit={confirmSetup} className="form-grid">
          {setup.qr_data_uri && (
            <img src={setup.qr_data_uri} alt="QR code MFA" className="mfa-qr" />
          )}
          <FormField label="Secret manuale">
            <p className="form-hint" style={{ margin: 0, wordBreak: "break-all" }}>
              <code>{setup.secret}</code>
            </p>
          </FormField>
          <FormField label="Codice authenticator" htmlFor="mfa-code">
            <input
              id="mfa-code"
              placeholder="123456"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 8))}
              inputMode="numeric"
              autoComplete="one-time-code"
              required
            />
          </FormField>
          <button type="submit" className="btn-primary" disabled={loading}>
            Attiva MFA
          </button>
        </form>
      )}
      {error && <p className="auth-error form-field-error">{error}</p>}
    </div>
  );
}
