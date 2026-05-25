import ipaddress
import json
import os
import re
from pathlib import Path
from typing import Any

import dns.exception
import dns.resolver

from app.db import DATA_DIR, GENERATED_DIR, db

POSTFIX_HOSTNAME = os.environ.get("POSTFIX_HOSTNAME", "mx1.example.com")
PUBLIC_HOSTNAME = os.environ.get("PUBLIC_HOSTNAME", "").strip() or None
PUBLIC_IPV4 = os.environ.get("PUBLIC_IPV4", "").strip()
DKIM_PUB_DIR = GENERATED_DIR / "dkim"
DEFAULT_DKIM_SELECTOR = os.environ.get("DKIM_SELECTOR", "mail")

def _resolver_nameservers() -> list[str]:
    try:
        from app.system_settings import get_docker_dns_servers

        return get_docker_dns_servers()
    except Exception:
        return ["208.67.222.222", "208.67.220.220"]


def _make_resolver() -> dns.resolver.Resolver:
    resolver = dns.resolver.Resolver()
    resolver.nameservers = _resolver_nameservers()
    resolver.lifetime = 8.0
    return resolver


def _txt_records(name: str) -> tuple[list[str], str | None]:
    try:
        answers = _make_resolver().resolve(name, "TXT")
    except dns.exception.DNSException as exc:
        return [], str(exc)

    records: list[str] = []
    for answer in answers:
        parts = []
        for chunk in answer.strings:
            if isinstance(chunk, bytes):
                parts.append(chunk.decode("utf-8", errors="replace"))
            else:
                parts.append(str(chunk))
        records.append("".join(parts))
    return records, None


def _normalize_pubkey(key: str | None) -> str | None:
    if not key:
        return None
    cleaned = re.sub(r"\s+", "", key.strip())
    return cleaned or None


def _extract_dkim_pubkey(txt_value: str) -> str | None:
    compact = re.sub(r"\s+", "", txt_value)
    match = re.search(r"p=([A-Za-z0-9+/=]+)", compact)
    return _normalize_pubkey(match.group(1)) if match else None


def _read_local_dkim_pubkey(domain: str) -> str | None:
    per_domain = DKIM_PUB_DIR / f"{domain.lower()}.pub"
    if per_domain.exists():
        return _normalize_pubkey(per_domain.read_text(encoding="utf-8"))
    legacy = DATA_DIR / "generated" / "dkim-public.key"
    if legacy.exists():
        return _normalize_pubkey(legacy.read_text(encoding="utf-8"))
    return None


def _format_dkim_txt_record(pubkey: str) -> str:
    return f"v=DKIM1; k=rsa; p={pubkey}"


def _dkim_expected_fields(local_pubkey: str | None) -> dict[str, Any]:
    if not local_pubkey:
        return {}
    return {"expected": _format_dkim_txt_record(local_pubkey)}


def _is_non_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
    )


def _parse_public_ipv4_env() -> list[str]:
    if not PUBLIC_IPV4:
        return []
    ips: list[str] = []
    for part in re.split(r"[\s,]+", PUBLIC_IPV4):
        part = part.strip()
        if not part:
            continue
        try:
            addr = ipaddress.ip_address(part)
        except ValueError:
            continue
        if addr.version != 4 or _is_non_public_ip(part):
            continue
        ips.append(str(addr))
    return sorted(set(ips))


def _resolve_hostname_via_public_dns(hostname: str) -> list[str]:
    ips: list[str] = []
    for rrtype in ("A", "AAAA"):
        try:
            answers = _make_resolver().resolve(hostname, rrtype)
        except dns.exception.DNSException:
            continue
        for answer in answers:
            ips.append(str(answer))
    return sorted(set(ips))


def _smtp_dns_hostname() -> str:
    if PUBLIC_HOSTNAME:
        return PUBLIC_HOSTNAME
    try:
        from app.system_settings import get_public_hostname

        return get_public_hostname()
    except Exception:
        return POSTFIX_HOSTNAME


def _resolve_smtp_host_ips() -> tuple[list[str], list[str]]:
    """Return (public_ips, private_ips) for SPF/DNS recommendations."""
    public: list[str] = []
    private: list[str] = []

    public.extend(_parse_public_ipv4_env())

    for ip in _resolve_hostname_via_public_dns(_smtp_dns_hostname()):
        if _is_non_public_ip(ip):
            private.append(ip)
        else:
            public.append(ip)

    return sorted(set(public)), sorted(set(private))


def _spf_includes_host(spf: str, hostname: str, host_ips: list[str]) -> bool:
    spf_lower = spf.lower()
    tokens = re.split(r"[\s:]+", spf_lower)
    if "mx" in tokens:
        return True
    if f"a:{hostname.lower()}" in spf_lower:
        return True
    if f"include:{hostname.lower()}" in spf_lower:
        return True
    for ip in host_ips:
        if ":" in ip:
            if f"ip6:{ip.lower()}" in spf_lower:
                return True
        elif f"ip4:{ip}" in spf_lower:
            return True
    return False


def _check_spf(domain: str) -> dict[str, Any]:
    name = domain
    records, error = _txt_records(name)
    host_ips, host_ips_private = _resolve_smtp_host_ips()
    suggested = f"v=spf1 mx a:{POSTFIX_HOSTNAME} -all"
    spf_ip_fields: dict[str, Any] = {"hostname_ips": host_ips}
    if host_ips_private:
        spf_ip_fields["hostname_ips_private"] = host_ips_private

    if error:
        return {
            "name": name,
            "status": "error",
            "found": [],
            "expected": suggested,
            "messages": [f"Query TXT fallita: {error}"],
            **spf_ip_fields,
        }

    spf_records = [r for r in records if r.lower().startswith("v=spf1")]
    if not spf_records:
        return {
            "name": name,
            "status": "error",
            "found": records,
            "expected": suggested,
            "messages": ["Record SPF assente (TXT con v=spf1)."],
            **spf_ip_fields,
        }

    spf = spf_records[0]
    messages: list[str] = []
    status = "ok"
    suggested_additions: list[str] = []

    if not _spf_includes_host(spf, POSTFIX_HOSTNAME, host_ips):
        status = "warning"
        messages.append(
            f"SPF presente ma non autorizza esplicitamente {POSTFIX_HOSTNAME}."
        )
        suggested_additions.append(f"a:{POSTFIX_HOSTNAME}")
        for ip in host_ips:
            if ":" in ip:
                suggested_additions.append(f"ip6:{ip}")
            else:
                suggested_additions.append(f"ip4:{ip}")
        if suggested_additions:
            messages.append(
                "Per invio da questo server aggiungi al record SPF: "
                + " ".join(suggested_additions)
                + " (prima di -all/~all)."
            )
    else:
        messages.append("Record SPF trovato e coerente con l'host SMTP configurato.")

    if "-all" not in spf and "~all" not in spf:
        status = "warning" if status == "ok" else status
        messages.append("Consigliato terminare SPF con -all (o ~all in fase di test).")

    return {
        "name": name,
        "status": status,
        "found": spf_records,
        "expected": suggested,
        "suggested_additions": suggested_additions,
        "messages": messages,
        **spf_ip_fields,
    }


def _check_dkim(domain: str, selector: str) -> dict[str, Any]:
    name = f"{selector}._domainkey.{domain}"
    records, error = _txt_records(name)
    local_pubkey = _read_local_dkim_pubkey(domain)

    if error:
        return {
            "name": name,
            "status": "error",
            "found": [],
            "expected_selector": selector,
            "local_public_key": local_pubkey,
            "messages": [f"Query TXT fallita: {error}"],
            **_dkim_expected_fields(local_pubkey),
        }

    dkim_records = [r for r in records if "v=DKIM1" in r]
    if not dkim_records:
        return {
            "name": name,
            "status": "error",
            "found": records,
            "expected_selector": selector,
            "local_public_key": local_pubkey,
            "messages": [f"Record DKIM assente su {name}."],
            **_dkim_expected_fields(local_pubkey),
        }

    dns_value = dkim_records[0]
    dns_pubkey = _extract_dkim_pubkey(dns_value)
    messages: list[str] = []
    status = "ok"

    if not dns_pubkey:
        status = "error"
        messages.append("Record DKIM trovato ma senza chiave pubblica (p=).")
    else:
        messages.append("Record DKIM trovato.")

    if local_pubkey:
        if dns_pubkey and dns_pubkey == local_pubkey:
            messages.append("Chiave pubblica DNS coincide con quella del server OpenDKIM.")
        elif dns_pubkey:
            status = "warning" if status == "ok" else status
            messages.append(
                "Chiave pubblica DNS diversa da quella attiva sul server: aggiorna il TXT o rigenera DKIM."
            )
    else:
        status = "warning" if status == "ok" else status
        messages.append(
            "Chiave locale non disponibile (OpenDKIM non ha ancora esportato la chiave per questo dominio)."
        )

    return {
        "name": name,
        "status": status,
        "found": dkim_records,
        "expected_selector": selector,
        "local_public_key": local_pubkey,
        "dns_public_key": dns_pubkey,
        "messages": messages,
        **_dkim_expected_fields(local_pubkey),
    }


def _check_dmarc(domain: str) -> dict[str, Any]:
    name = f"_dmarc.{domain}"
    records, error = _txt_records(name)
    suggested = f"v=DMARC1; p=none; rua=mailto:dmarc@{domain}"

    if error:
        return {
            "name": name,
            "status": "error",
            "found": [],
            "expected": suggested,
            "messages": [f"Query TXT fallita: {error}"],
        }

    dmarc_records = [r for r in records if "v=dmarc1" in r.lower()]
    if not dmarc_records:
        return {
            "name": name,
            "status": "error",
            "found": records,
            "expected": suggested,
            "messages": [f"Record DMARC assente su {name}."],
        }

    dmarc = dmarc_records[0]
    messages = ["Record DMARC trovato."]
    status = "ok"

    policy_match = re.search(r"\bp=([a-zA-Z]+)", dmarc, re.IGNORECASE)
    if not policy_match:
        status = "warning"
        messages.append("DMARC senza policy p= (none/quarantine/reject).")
    elif policy_match.group(1).lower() == "none":
        messages.append("Policy attuale: p=none (ok per test, in produzione valuta quarantine/reject).")

    if "rua=" not in dmarc.lower():
        status = "warning" if status == "ok" else status
        messages.append("Consigliato aggiungere rua= per reportistica DMARC.")

    return {
        "name": name,
        "status": status,
        "found": dmarc_records,
        "expected": suggested,
        "messages": messages,
    }


def _read_mx_hints(domain: str) -> list[dict[str, Any]]:
    with db() as conn:
        row = conn.execute(
            "SELECT dns_mx_hints FROM domains WHERE name = ? COLLATE NOCASE",
            (domain.strip().lower(),),
        ).fetchone()
    if not row or not row["dns_mx_hints"]:
        return []
    try:
        data = json.loads(row["dns_mx_hints"])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    hints: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        host = str(item.get("host") or "").strip().lower().rstrip(".")
        if not host:
            continue
        hints.append({"priority": int(item.get("priority") or 10), "host": host})
    return sorted(hints, key=lambda row: (row["priority"], row["host"]))


def _format_mx_expected(records: list[dict[str, Any]]) -> list[str]:
    return [f"{int(row['priority'])} {row['host']}" for row in records]


def _check_mx(domain: str, hints: list[dict[str, Any]]) -> dict[str, Any]:
    local_records = []
    for priority, host in ((10, POSTFIX_HOSTNAME), (20, PUBLIC_HOSTNAME or "")):
        normalized = (host or "").strip().lower().rstrip(".")
        if normalized and normalized not in {row["host"] for row in local_records}:
            local_records.append({"priority": priority, "host": normalized})

    expected_by_host = {row["host"]: row for row in local_records}
    for hint in hints:
        expected_by_host.setdefault(hint["host"], hint)
    expected = sorted(expected_by_host.values(), key=lambda row: (row["priority"], row["host"]))

    try:
        answers = _make_resolver().resolve(domain, "MX")
    except dns.exception.DNSException as exc:
        return {
            "name": domain,
            "status": "error",
            "found": [],
            "expected": _format_mx_expected(expected),
            "cluster_hints": hints,
            "messages": [f"Query MX fallita: {exc}"],
        }

    found: list[str] = []
    found_hosts: set[str] = set()
    for answer in answers:
        host = str(answer.exchange).strip().lower().rstrip(".")
        found.append(f"{int(answer.preference)} {host}")
        found_hosts.add(host)

    messages: list[str] = []
    status = "ok"
    missing = [row for row in expected if row["host"] not in found_hosts]
    if missing:
        status = "warning"
        messages.append(
            "Record MX mancanti per il cluster: "
            + ", ".join(_format_mx_expected(missing))
        )
    elif found:
        messages.append("Record MX trovati.")
    else:
        status = "error"
        messages.append("Nessun record MX pubblicato per il dominio.")

    return {
        "name": domain,
        "status": status,
        "found": sorted(found),
        "expected": _format_mx_expected(expected),
        "cluster_hints": hints,
        "messages": messages,
    }


def check_dns_for_domain(
    domain: str, dkim_selector: str | None = None
) -> dict[str, Any]:
    domain = domain.strip().lower()
    selector = dkim_selector or DEFAULT_DKIM_SELECTOR
    with db() as conn:
        row = conn.execute(
            "SELECT dkim_selector FROM domains WHERE name = ? COLLATE NOCASE",
            (domain,),
        ).fetchone()
    if row:
        selector = row["dkim_selector"] or selector

    smtp_hostname = _smtp_dns_hostname()
    mx_hints = _read_mx_hints(domain)
    return {
        "domain": domain,
        "hostname": POSTFIX_HOSTNAME,
        "smtp_hostname": smtp_hostname,
        "dkim_selector": selector,
        "spf": _check_spf(domain),
        "mx": _check_mx(domain, mx_hints),
        "dkim": _check_dkim(domain, selector),
        "dmarc": _check_dmarc(domain),
    }


def check_all_domains() -> dict[str, Any]:
    with db() as conn:
        rows = conn.execute(
            "SELECT name, dkim_selector, enabled FROM domains ORDER BY name"
        ).fetchall()

    checks = [
        check_dns_for_domain(row["name"], row["dkim_selector"])
        for row in rows
    ]
    return {
        "hostname": POSTFIX_HOSTNAME,
        "domains": checks,
        "spamassassin_scope": "global",
    }
