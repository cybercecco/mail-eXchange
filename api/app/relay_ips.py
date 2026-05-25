"""Validate and normalize per-domain relay source IP/CIDR lists.

Outbound relay policy (Postfix smtpd_relay_restrictions):
  (a) SASL authentication on submission (587), or
  (b) client IP matches relay_source_ips for the envelope sender @domain.

Inbound MX delivery to local virtual domains is unaffected (relay restrictions
apply only when the recipient is not local).
"""

from __future__ import annotations

import ipaddress
import re
from typing import Optional

from fastapi import HTTPException

MAX_RELAY_SOURCE_IPS = 64

_SPLIT_RE = re.compile(r"[\s,;]+")


def parse_relay_source_ips_text(value: str | None) -> list[str]:
    """Split multiline / comma-separated input into non-empty tokens."""
    if not value or not str(value).strip():
        return []
    return [part.strip() for part in _SPLIT_RE.split(str(value).strip()) if part.strip()]


def normalize_relay_source_ips(
    value: str | list[str] | None,
    *,
    max_entries: int = MAX_RELAY_SOURCE_IPS,
) -> list[str]:
    """Parse, validate, dedupe and canonicalize IP/CIDR entries."""
    if value is None:
        return []
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
    else:
        parts = parse_relay_source_ips_text(str(value))

    if len(parts) > max_entries:
        raise HTTPException(
            status_code=400,
            detail=f"At most {max_entries} relay source IPs/CIDRs allowed",
        )

    normalized: list[str] = []
    seen: set[str] = set()
    for part in parts:
        try:
            network = ipaddress.ip_network(part, strict=False)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid relay source IP or CIDR: {part}",
            ) from exc
        canonical = str(network)
        if canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)
    return normalized


def relay_source_ips_from_db(raw: Optional[str]) -> list[str]:
    """Load stored newline-separated CIDRs (already normalized at write time)."""
    if not raw or not str(raw).strip():
        return []
    return [line.strip() for line in str(raw).splitlines() if line.strip()]


def relay_source_ips_to_db(ips: list[str]) -> Optional[str]:
    if not ips:
        return None
    return "\n".join(ips)


def relay_client_access_filename(domain_name: str) -> str:
    """Safe basename for per-domain Postfix cidr map files."""
    return "relay_client_access_" + domain_name.strip().lower().replace(".", "_") + ".cidr"
