"""Apply generated Docker DNS override and reload Caddy via docker compose (admin settings)."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from app.db import GENERATED_DIR
from app.service_restart import _docker_socket_path, docker_restart_available
from app.system_settings import MX_SERVICES

_APPLY_LOCK = threading.Lock()
_APPLY_TIMEOUT = float(os.environ.get("DOCKER_DNS_APPLY_TIMEOUT", "180"))
_COMPOSE_DIR = Path(os.environ.get("COMPOSE_PROJECT_DIR", "/compose"))
_COMPOSE_FILE = _COMPOSE_DIR / "docker-compose.yml"
_DNS_OVERRIDE = GENERATED_DIR / "docker-dns.override.yml"
_CADDY_CONTAINER = os.environ.get("CADDY_CONTAINER", "mx-caddy")
_API_CONTAINER = os.environ.get("API_CONTAINER", "mx-api")


def _docker_env() -> dict[str, str]:
    env = os.environ.copy()
    env["DOCKER_HOST"] = f"unix://{_docker_socket_path()}"
    return env


def _compose_project_name() -> str:
    configured = os.environ.get("COMPOSE_PROJECT_NAME", "").strip()
    if configured:
        return configured
    try:
        proc = subprocess.run(
            [
                "docker",
                "inspect",
                _API_CONTAINER,
                "--format",
                '{{ index .Config.Labels "com.docker.compose.project" }}',
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=_docker_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "mail-exchange"
    name = (proc.stdout or "").strip()
    return name or "mail-exchange"


def _compose_cmd() -> list[str] | None:
    if shutil.which("docker") is None:
        return None
    try:
        proc = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            timeout=10,
            env=_docker_env(),
            check=False,
        )
        if proc.returncode == 0:
            return ["docker", "compose"]
    except subprocess.TimeoutExpired:
        pass
    if shutil.which("docker-compose") is not None:
        return ["docker-compose"]
    return None


def _recreate_api_container(
    compose: list[str],
    env: dict[str, str],
    *,
    delay_seconds: float = 2.0,
) -> None:
    def _run() -> None:
        time.sleep(delay_seconds)
        subprocess.run(
            [
                *compose,
                "--project-directory",
                str(_COMPOSE_DIR),
                "-f",
                str(_COMPOSE_FILE),
                "-f",
                str(_DNS_OVERRIDE),
                "up",
                "-d",
                "--no-build",
                "api",
            ],
            capture_output=True,
            text=True,
            timeout=_APPLY_TIMEOUT,
            env=env,
            check=False,
        )

    threading.Thread(target=_run, daemon=True).start()


def apply_docker_stack_settings() -> dict[str, Any]:
    """Recreate DNS-tagged services and restart Caddy after settings regenerate."""
    result: dict[str, Any] = {
        "dns_applied": False,
        "dns_apply_message": "",
        "caddy_reloaded": False,
    }

    if not docker_restart_available():
        result["dns_apply_message"] = (
            "Impostazioni salvate. Docker non disponibile: DNS non applicati automaticamente."
        )
        return result

    if not _COMPOSE_FILE.is_file():
        result["dns_apply_message"] = (
            f"Impostazioni salvate. File compose non trovato ({_COMPOSE_FILE}): "
            "montare la directory del progetto nel container API."
        )
        return result

    if not _DNS_OVERRIDE.is_file():
        result["dns_apply_message"] = (
            "Impostazioni salvate. Override DNS generato non trovato."
        )
        return result

    compose = _compose_cmd()
    if compose is None:
        result["dns_apply_message"] = (
            "Impostazioni salvate. Docker Compose non disponibile nel container API."
        )
        return result

    if not _APPLY_LOCK.acquire(blocking=False):
        result["dns_apply_message"] = (
            "Impostazioni salvate. Applicazione DNS già in corso, riprova tra poco."
        )
        return result

    env = _docker_env()
    env["COMPOSE_PROJECT_NAME"] = _compose_project_name()

    try:
        services = [s for s in MX_SERVICES if s != "api"]
        up_cmd = [
            *compose,
            "--project-directory",
            str(_COMPOSE_DIR),
            "-f",
            str(_COMPOSE_FILE),
            "-f",
            str(_DNS_OVERRIDE),
            "up",
            "-d",
            "--no-build",
            *services,
        ]
        try:
            proc = subprocess.run(
                up_cmd,
                capture_output=True,
                text=True,
                timeout=_APPLY_TIMEOUT,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            result["dns_apply_message"] = (
                f"Impostazioni salvate. Timeout applicazione DNS ({int(_APPLY_TIMEOUT)}s)."
            )
            return result

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            result["dns_apply_message"] = (
                "Impostazioni salvate. Applicazione DNS fallita"
                + (f": {detail}" if detail else ".")
            )
            return result

        try:
            caddy = subprocess.run(
                ["docker", "restart", _CADDY_CONTAINER],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            result["dns_applied"] = True
            result["dns_apply_message"] = (
                "DNS applicati ai container. Timeout riavvio Caddy per il nuovo Caddyfile."
            )
            return result

        if caddy.returncode != 0:
            detail = (caddy.stderr or caddy.stdout or "").strip()
            result["dns_applied"] = True
            result["dns_apply_message"] = (
                "DNS applicati ai container. Riavvio Caddy fallito"
                + (f": {detail}" if detail else ".")
            )
            return result

        result["dns_applied"] = True
        result["caddy_reloaded"] = True
        _recreate_api_container(compose, env)
        result["dns_apply_message"] = (
            "DNS applicati ai container e Caddy ricaricato con il nuovo Caddyfile. "
            "Il container API verrà aggiornato tra pochi secondi."
        )
        return result
    finally:
        _APPLY_LOCK.release()
