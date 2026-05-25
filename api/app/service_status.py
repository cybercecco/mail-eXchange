"""Operational checks for Mail Exchange stack services (Docker network probes)."""

from __future__ import annotations

import os
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

DEFAULT_TIMEOUT = float(os.environ.get("SERVICE_CHECK_TIMEOUT", "2.5"))
CLAMAV_TIMEOUT = float(os.environ.get("CLAMAV_CHECK_TIMEOUT", "4.0"))


def _tcp_reachable(host: str, port: int, timeout: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"Porta {port} raggiungibile"
    except OSError as exc:
        return False, str(exc) or "Connessione rifiutata"


def _check_clamd(host: str, port: int, timeout: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(b"zPING\0")
            sock.settimeout(timeout)
            data = sock.recv(32)
        if b"PONG" in data:
            return True, "ClamAV risponde a PING"
        return False, "Risposta ClamAV inattesa"
    except OSError as exc:
        return False, str(exc) or "ClamAV non raggiungibile"


def _check_http(host: str, port: int, timeout: float) -> tuple[bool, str]:
    ok, detail = _tcp_reachable(host, port, timeout)
    if not ok:
        return ok, detail
    try:
        import urllib.request

        url = f"http://{host}:{port}/"
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status < 500:
                return True, f"HTTP {resp.status}"
    except Exception:
        return True, f"Porta {port} raggiungibile"
    return True, f"Porta {port} raggiungibile"


def _probe(defn: dict[str, Any]) -> dict[str, Any]:
    check_id = defn["id"]
    label = defn["label"]
    kind = defn.get("kind", "tcp")
    host = defn.get("host", "")
    port = int(defn.get("port", 0))
    timeout = float(defn.get("timeout", DEFAULT_TIMEOUT))

    base = {
        "container": defn.get("container"),
        "restartable": bool(defn.get("restartable", False)),
    }

    if kind == "self":
        return {
            "id": check_id,
            "label": label,
            "role": defn.get("role", ""),
            "status": "ok",
            "detail": "Processo API attivo",
            **base,
        }

    if kind == "clamd":
        ok, detail = _check_clamd(host, port, timeout)
    elif kind == "http":
        ok, detail = _check_http(host, port, timeout)
    else:
        ok, detail = _tcp_reachable(host, port, timeout)

    return {
        "id": check_id,
        "label": label,
        "role": defn.get("role", ""),
        "status": "ok" if ok else "down",
        "detail": detail,
        **base,
    }


DAEMON_CHECKS: tuple[dict[str, Any], ...] = (
    {
        "id": "api",
        "label": "API pannello",
        "role": "Backend FastAPI, database e configurazione",
        "kind": "self",
        "container": "mx-api",
        "restartable": True,
    },
    {
        "id": "frontend",
        "label": "Frontend web",
        "role": "Interfaccia React servita da nginx",
        "host": os.environ.get("FRONTEND_HOST", "frontend"),
        "port": int(os.environ.get("FRONTEND_PORT", "80")),
        "kind": "http",
        "container": "mx-frontend",
        "restartable": True,
    },
    {
        "id": "caddy",
        "label": "Caddy",
        "role": "Reverse proxy HTTPS e certificati ACME",
        "host": os.environ.get("CADDY_HOST", "caddy"),
        "port": int(os.environ.get("CADDY_PORT", "80")),
        "kind": "http",
        "container": "mx-caddy",
        "restartable": True,
    },
    {
        "id": "postfix",
        "label": "Postfix",
        "role": "MTA SMTP in ingresso e in uscita",
        "host": os.environ.get("POSTFIX_SMTP_HOST", "postfix"),
        "port": int(os.environ.get("POSTFIX_SMTP_PORT", "25")),
        "container": "mx-postfix",
        "restartable": True,
    },
    {
        "id": "amavis",
        "label": "Amavis",
        "role": "Antispam e filtro contenuti (SpamAssassin)",
        "host": os.environ.get("AMAVIS_HOST", "amavis"),
        "port": int(os.environ.get("AMAVIS_PORT", "10024")),
        "container": "mx-amavis",
        "restartable": True,
    },
    {
        "id": "clamav",
        "label": "ClamAV",
        "role": "Antivirus (clamd)",
        "host": os.environ.get("CLAMAV_HOST", "clamav"),
        "port": int(os.environ.get("CLAMAV_PORT", "3310")),
        "kind": "clamd",
        "timeout": CLAMAV_TIMEOUT,
        "container": "mx-clamav",
        "restartable": True,
    },
    {
        "id": "opendkim",
        "label": "OpenDKIM",
        "role": "Firma DKIM in uscita (milter)",
        "host": os.environ.get("OPENDKIM_HOST", "opendkim"),
        "port": int(os.environ.get("OPENDKIM_PORT", "8891")),
        "container": "mx-opendkim",
        "restartable": True,
    },
)


def get_daemon_defn(daemon_id: str) -> dict[str, Any] | None:
    for defn in DAEMON_CHECKS:
        if defn["id"] == daemon_id:
            return defn
    return None


def collect_daemon_status() -> dict[str, Any]:
    daemons: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(DAEMON_CHECKS)) as pool:
        futures = {pool.submit(_probe, d): d["id"] for d in DAEMON_CHECKS}
        by_id: dict[str, dict[str, Any]] = {}
        for fut in as_completed(futures):
            by_id[futures[fut]] = fut.result()
    order = [d["id"] for d in DAEMON_CHECKS]
    daemons = [by_id[i] for i in order]

    down = sum(1 for d in daemons if d["status"] == "down")
    overall = "ok" if down == 0 else "degraded"

    return {
        "status": overall,
        "daemons": daemons,
        "summary": {
            "total": len(daemons),
            "operational": len(daemons) - down,
            "down": down,
        },
    }
