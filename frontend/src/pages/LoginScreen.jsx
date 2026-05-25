import { useState } from "react";
import { api, setToken } from "../api";
import { FormField } from "../components/FormField";
import { useTheme } from "../hooks/useTheme";

export default function LoginScreen({ onLoggedIn }) {
  const { theme, toggleTheme } = useTheme();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [tempToken, setTempToken] = useState(null);
  const [pendingUser, setPendingUser] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleLogin(event) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const result = await api("/auth/login", {
        publicAuth: true,
        method: "POST",
        body: JSON.stringify({ username, password })
      });
      if (result.mfa_required) {
        setTempToken(result.temp_token);
        setPendingUser(result.user);
        return;
      }
      setToken(result.access_token);
      onLoggedIn(result.user);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleMfa(event) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const result = await api("/auth/mfa/verify", {
        publicAuth: true,
        method: "POST",
        body: JSON.stringify({ temp_token: tempToken, code: mfaCode })
      });
      setToken(result.access_token);
      onLoggedIn(result.user);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="auth-card-header">
          <div>
            <h1>{tempToken ? "Verifica MFA" : "Mail Exchange"}</h1>
            <p className="subtitle">
              {tempToken
                ? <>Codice a 6 cifre per <strong>{pendingUser?.username}</strong></>
                : "Accedi per gestire domini e caselle"}
            </p>
          </div>
          <button type="button" className="btn-ghost btn-sm" onClick={toggleTheme} title="Cambia tema">
            {theme === "dark" ? "☀" : "☾"}
          </button>
        </div>

        {tempToken ? (
          <form onSubmit={handleMfa} className="auth-form">
            <FormField label="Codice MFA" htmlFor="login-mfa">
              <input
                id="login-mfa"
                placeholder="123456"
                value={mfaCode}
                onChange={(e) => setMfaCode(e.target.value.replace(/\D/g, "").slice(0, 8))}
                inputMode="numeric"
                autoComplete="one-time-code"
                required
              />
            </FormField>
            {error && <p className="auth-error">{error}</p>}
            <button type="submit" className="btn-primary" disabled={loading}>
              {loading ? "Verifica..." : "Accedi"}
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                setTempToken(null);
                setMfaCode("");
                setError("");
              }}
            >
              Indietro
            </button>
          </form>
        ) : (
          <form onSubmit={handleLogin} className="auth-form">
            <FormField label="Username" htmlFor="login-username">
              <input
                id="login-username"
                placeholder="admin"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
              />
            </FormField>
            <FormField label="Password" htmlFor="login-password">
              <input
                id="login-password"
                type="password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </FormField>
            {error && <p className="auth-error">{error}</p>}
            <button type="submit" className="btn-primary" disabled={loading}>
              {loading ? "Accesso..." : "Accedi"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
