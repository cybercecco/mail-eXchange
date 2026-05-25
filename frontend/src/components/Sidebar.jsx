const NAV = [
  {
    id: "mail",
    label: "Mail",
    items: [{ id: "domains", label: "Domini" }]
  },
  {
    id: "security",
    label: "Sicurezza",
    items: [
      { id: "dns", label: "DNS (SPF/DKIM/DMARC)" },
      { id: "spam", label: "SpamAssassin" }
    ]
  },
  {
    id: "system",
    label: "Sistema",
    items: [
      { id: "traffic", label: "Traffico" },
      { id: "profile", label: "Il mio account" },
      { id: "status", label: "Stato & sessione" }
    ]
  }
];

const CONFIG_NAV = {
  id: "config",
  label: "Configurazione",
  items: [
    { id: "settings", label: "Sistema & test mail" },
    { id: "users", label: "Utenti" }
  ]
};

export { NAV };

export default function Sidebar({
  id = "app-sidebar",
  activePage,
  onNavigate,
  user,
  theme,
  onToggleTheme,
  onLogout,
  isAdmin
}) {
  const sections = isAdmin ? [...NAV, CONFIG_NAV] : NAV;
  return (
    <aside id={id} className="sidebar">
      <div className="sidebar-brand">
        <h1>Mail Exchange</h1>
        <p>Control plane</p>
      </div>

      <nav className="sidebar-nav" aria-label="Navigazione principale">
        {sections.map((section) => (
          <div key={section.id} className="nav-section">
            <span className="nav-section-title">{section.label}</span>
            <div className="nav-section-items">
              {section.items.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`nav-item${activePage === item.id ? " active" : ""}`}
                  onClick={() => onNavigate(item.id)}
                  aria-current={activePage === item.id ? "page" : undefined}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div
        className={`sidebar-footer${user.mfa_enabled ? "" : " sidebar-footer--mfa-warning"}`}
        role={user.mfa_enabled ? undefined : "alert"}
        aria-live={user.mfa_enabled ? undefined : "polite"}
      >
        <div className="sidebar-user">
          <strong>{user.username}</strong>
          <span>
            {user.role}
            {user.mfa_enabled ? (
              <span className="sidebar-mfa-ok"> · MFA attivo</span>
            ) : (
              <span className="sidebar-mfa-missing"> · MFA non attivo</span>
            )}
          </span>
        </div>
        <div className="theme-toggle">
          <span>Tema {theme === "dark" ? "scuro" : "chiaro"}</span>
          <button type="button" className="btn-secondary btn-sm" onClick={onToggleTheme}>
            {theme === "dark" ? "Chiaro" : "Scuro"}
          </button>
        </div>
        <button type="button" className="btn-danger" onClick={onLogout}>
          Esci
        </button>
      </div>
    </aside>
  );
}
