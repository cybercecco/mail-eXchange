export default function StatusBadge({ status }) {
  const label = status === "ok" ? "OK" : status === "warning" ? "Attenzione" : "Errore";
  const cls = status === "ok" ? "ok" : status === "warning" ? "warning" : "error";
  return <span className={`status-badge ${cls}`}>{label}</span>;
}
