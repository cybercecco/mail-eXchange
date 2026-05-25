import { useEffect, useState } from "react";
import { api, getToken, setToken } from "./api";
import Dashboard from "./Dashboard";
import LoginScreen from "./pages/LoginScreen";

export default function App() {
  const [authState, setAuthState] = useState("loading");
  const [user, setUser] = useState(null);

  useEffect(() => {
    const path = window.location.pathname.replace(/\/+$/, "") || "/";
    if (path === "/mailboxes") {
      sessionStorage.setItem("mx-open-tab", "caselle");
      window.history.replaceState(null, "", "/domains");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function restore() {
      if (!getToken()) {
        if (!cancelled) setAuthState("anonymous");
        return;
      }
      try {
        const me = await api("/auth/me");
        if (!cancelled) {
          setUser(me);
          setAuthState("authenticated");
        }
      } catch {
        setToken(null);
        if (!cancelled) setAuthState("anonymous");
      }
    }
    restore();
    return () => {
      cancelled = true;
    };
  }, []);

  function handleLogout() {
    setToken(null);
    setUser(null);
    setAuthState("anonymous");
  }

  if (authState === "loading") {
    return <div className="auth-screen auth-loading">Caricamento...</div>;
  }

  if (authState !== "authenticated") {
    return (
      <LoginScreen
        onLoggedIn={(u) => {
          setUser(u);
          setAuthState("authenticated");
        }}
      />
    );
  }

  return (
    <Dashboard
      user={user}
      onLogout={handleLogout}
      onUserUpdate={(u) => setUser(u)}
    />
  );
}
