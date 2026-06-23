"""IMAP targets for SMTP submission auth (mobile relay via MDaemon)."""

from __future__ import annotations

DEFAULT_IMAP_AUTH_PORT = 993


def resolve_imap_target(
    smtp_host: str,
    smtp_port: int,
    imap_auth_host: str | None,
    imap_auth_port: int | None,
) -> dict[str, str | int | bool]:
    host = (imap_auth_host or smtp_host).strip().lower()
    port = int(imap_auth_port) if imap_auth_port is not None else DEFAULT_IMAP_AUTH_PORT
    return {"host": host, "port": port, "ssl": port == 993}


def build_imap_auth_config(conn) -> dict[str, dict[str, dict]]:
    """Build user/domain -> IMAP server map for Postfix SASL (pam_exec)."""
    users_map: dict[str, dict] = {}
    domains_map: dict[str, dict] = {}

    domain_rows = conn.execute(
        """
        SELECT id, name, relay_all_inbound
        FROM domains
        WHERE enabled = 1
        ORDER BY name
        """
    ).fetchall()
    dest_rows = conn.execute(
        """
        SELECT domain_id, host, port, imap_auth_host, imap_auth_port
        FROM domain_destinations
        ORDER BY domain_id, id
        """
    ).fetchall()
    mailbox_rows = conn.execute(
        """
        SELECT m.email, m.destination_host, m.destination_port,
               dd.imap_auth_host, dd.imap_auth_port
        FROM mailboxes m
        INNER JOIN domains d ON d.id = m.domain_id
        LEFT JOIN domain_destinations dd
          ON dd.domain_id = m.domain_id
         AND lower(trim(dd.host)) = lower(trim(m.destination_host))
         AND dd.port = m.destination_port
        WHERE m.enabled = 1 AND d.enabled = 1
        ORDER BY m.email
        """
    ).fetchall()

    dests_by_domain: dict[int, list] = {}
    for dest in dest_rows:
        dests_by_domain.setdefault(int(dest["domain_id"]), []).append(dest)

    for row in domain_rows:
        domain_id = int(row["id"])
        name = row["name"].strip().lower()
        dests = dests_by_domain.get(domain_id, [])
        if not dests:
            continue
        primary = dests[0]
        domains_map[name] = resolve_imap_target(
            primary["host"],
            int(primary["port"]),
            primary["imap_auth_host"],
            primary["imap_auth_port"],
        )

    for row in mailbox_rows:
        email = row["email"].strip().lower()
        users_map[email] = resolve_imap_target(
            row["destination_host"],
            int(row["destination_port"]),
            row["imap_auth_host"],
            row["imap_auth_port"],
        )

    return {"users": users_map, "domains": domains_map}
