import DnsRecordCard from "./DnsRecordCard";

export default function DomainDnsPanel({ check }) {
  return (
    <div className="dns-domain-block">
      <h3 style={{ marginTop: 0 }}>{check.domain}</h3>
      <p style={{ fontSize: "0.875rem", color: "var(--text-muted)", margin: "0 0 1rem" }}>
        Nome record DKIM: <code>{check.dkim_selector}._domainkey.{check.domain}</code>
      </p>
      <DnsRecordCard title="SPF" record={check.spf} />
      <DnsRecordCard
        title="DKIM"
        record={check.dkim}
        expectedLabel="Record TXT da configurare:"
        expectedAsPre
      />
      <DnsRecordCard title="DMARC" record={check.dmarc} />
    </div>
  );
}
