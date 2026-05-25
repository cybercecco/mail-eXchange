import StatusBadge from "./StatusBadge";

export default function DnsRecordCard({
  title,
  record,
  expectedLabel = "Suggerito:",
  expectedAsPre = false
}) {
  if (!record) return null;
  return (
    <div className="dns-record-card">
      <div className="dns-record-header">
        <h4>{title}</h4>
        <StatusBadge status={record.status} />
      </div>
      <p className="dns-record-name">{record.name}</p>
      <ul className="dns-record-messages">
        {record.messages.map((msg, idx) => (
          <li key={idx}>{msg}</li>
        ))}
      </ul>
      {record.found?.length > 0 && (
        <details style={{ marginTop: "0.5rem" }}>
          <summary style={{ cursor: "pointer", fontSize: "0.85rem", color: "var(--text-muted)" }}>
            Valore DNS rilevato
          </summary>
          <pre className="dns-found-pre">{record.found.join("\n")}</pre>
        </details>
      )}
      {record.suggested_additions?.length > 0 && (
        <p style={{ marginTop: "0.5rem", fontSize: "0.875rem" }}>
          <strong>Da aggiungere al SPF:</strong>{" "}
          <code>{record.suggested_additions.join(" ")}</code>
        </p>
      )}
      {record.expected && expectedAsPre ? (
        <div style={{ marginTop: "0.5rem" }}>
          <p style={{ margin: "0 0 0.35rem", fontSize: "0.875rem" }}>
            <strong>{expectedLabel}</strong>
          </p>
          <pre className="dns-found-pre">{record.expected}</pre>
        </div>
      ) : null}
      {record.expected && !expectedAsPre ? (
        <p style={{ marginTop: "0.5rem", fontSize: "0.875rem" }}>
          <strong>{expectedLabel}</strong>{" "}
          <code style={{ wordBreak: "break-word" }}>{record.expected}</code>
        </p>
      ) : null}
      {record.expected_selector && !record.expected ? (
        <p style={{ marginTop: "0.5rem", fontSize: "0.875rem" }}>
          <strong>Selector atteso:</strong> <code>{record.expected_selector}</code>
        </p>
      ) : null}
      {record.hostname_ips?.length > 0 && (
        <p style={{ marginTop: "0.5rem", fontSize: "0.875rem" }}>
          <strong>IP pubblici host SMTP:</strong> {record.hostname_ips.join(", ")}
        </p>
      )}
      {record.hostname_ips_private?.length > 0 && (
        <p style={{ marginTop: "0.5rem", fontSize: "0.875rem", color: "var(--text-muted)" }}>
          <strong>IP interni rilevati (non usare in SPF):</strong>{" "}
          {record.hostname_ips_private.join(", ")}
        </p>
      )}
    </div>
  );
}
