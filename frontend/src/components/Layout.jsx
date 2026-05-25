import { useEffect, useState } from "react";
import Sidebar from "./Sidebar";

function MenuIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M4 7h16M4 12h16M4 17h16" strokeLinecap="round" />
    </svg>
  );
}

export default function Layout({
  activePage,
  onNavigate,
  user,
  theme,
  onToggleTheme,
  onLogout,
  children
}) {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    function onResize() {
      if (window.innerWidth > 768) setSidebarOpen(false);
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (sidebarOpen) {
      document.body.classList.add("sidebar-scroll-lock");
    } else {
      document.body.classList.remove("sidebar-scroll-lock");
    }
    return () => document.body.classList.remove("sidebar-scroll-lock");
  }, [sidebarOpen]);

  function handleNavigate(page) {
    onNavigate(page);
    setSidebarOpen(false);
  }

  return (
    <div className={`app-shell${sidebarOpen ? " sidebar-open" : ""}`}>
      {sidebarOpen && (
        <button
          type="button"
          className="sidebar-backdrop"
          aria-label="Chiudi menu"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <Sidebar
        id="app-sidebar"
        activePage={activePage}
        onNavigate={handleNavigate}
        user={user}
        theme={theme}
        onToggleTheme={onToggleTheme}
        onLogout={onLogout}
        isAdmin={user.role === "admin"}
      />
      <div className="main-content">
        <header className="mobile-top-bar">
          <button
            type="button"
            className="sidebar-toggle btn-ghost"
            aria-expanded={sidebarOpen}
            aria-controls="app-sidebar"
            aria-label={sidebarOpen ? "Chiudi menu" : "Apri menu di navigazione"}
            onClick={() => setSidebarOpen((open) => !open)}
          >
            <MenuIcon />
          </button>
          <span className="mobile-top-bar__title">Mail Exchange</span>
        </header>
        {children}
      </div>
    </div>
  );
}
