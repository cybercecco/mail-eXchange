"""Apply generated Docker DNS override and reload Caddy via docker compose (admin settings)."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
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
_CADDY_CONFIG = os.environ.get(
    "CADDY_CONFIG_PATH", "/mail-exchange-data/generated/Caddyfile"
)
_API_CONTAINER = os.environ.get("API_CONTAINER", "mx-api")


@dataclass(frozen=True)
class SettingsInfraChanges:
    public_url: bool = False
    acme_email: bool = False
    docker_dns: bool = False

    @property
    def caddy_config(self) -> bool:
        return self.public_url or self.acme_email

    @property
    def any_change(self) -> bool:
        return self.caddy_config or self.docker_dns


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


def _docker_exec(
    container: str,
    args: list[str],
    *,
    timeout: float = 60,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", container, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_docker_env(),
        check=False,
    )


def validate_caddy_config() -> tuple[bool, str]:
    """Validate generated Caddyfile inside the running Caddy container."""
    if not docker_restart_available():
        return True, ""
    proc = _docker_exec(
        _CADDY_CONTAINER,
        [
            "caddy",
            "validate",
            "--config",
            _CADDY_CONFIG,
            "--adapter",
            "caddyfile",
        ],
    )
    if proc.returncode == 0:
        return True, ""
    detail = (proc.stderr or proc.stdout or "").strip()
    return False, detail or "Configurazione Caddy non valida"


def reload_caddy_config() -> tuple[bool, str]:
    """Gracefully reload Caddy with the generated Caddyfile."""
    ok, detail = validate_caddy_config()
    if not ok:
        return False, detail

    proc = _docker_exec(
        _CADDY_CONTAINER,
        [
            "caddy",
            "reload",
            "--config",
            _CADDY_CONFIG,
            "--adapter",
            "caddyfile",
        ],
    )
    if proc.returncode == 0:
        return True, ""
    detail = (proc.stderr or proc.stdout or "").strip()
    return False, detail or "Ricarica Caddy fallita"


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


def _apply_docker_dns_settings(
    compose: list[str],
    env: dict[str, str],
) -> tuple[bool, str, bool]:
    """Recreate DNS-tagged services. Returns (ok, message, caddy_recreated)."""
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
        return (
            False,
            f"Timeout applicazione DNS ({int(_APPLY_TIMEOUT)}s).",
            False,
        )

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return (
            False,
            "Applicazione DNS fallita" + (f": {detail}" if detail else "."),
            False,
        )

    _recreate_api_container(compose, env)
    return (
        True,
        "DNS applicati ai container. Il container API verrà aggiornato tra pochi secondi.",
        "caddy" in services,
    )


def apply_settings_changes(changes: SettingsInfraChanges) -> dict[str, Any]:
    """Apply only the infrastructure updates required by changed settings."""
    result: dict[str, Any] = {
        "dns_applied": False,
        "dns_apply_message": "",
        "caddy_reloaded": False,
        "apply_failed": False,
    }

    if not changes.any_change:
        result["dns_apply_message"] = "Impostazioni salvate."
        return result

    if not docker_restart_available():
        result["dns_apply_message"] = (
            "Impostazioni salvate. Docker non disponibile: "
            "modifiche non applicate automaticamente."
        )
        if changes.caddy_config:
            result["apply_failed"] = True
        return result

    if changes.docker_dns:
        if not _COMPOSE_FILE.is_file():
            result["dns_apply_message"] = (
                f"Impostazioni salvate. File compose non trovato ({_COMPOSE_FILE})."
            )
            result["apply_failed"] = True
            return result
        if not _DNS_OVERRIDE.is_file():
            result["dns_apply_message"] = (
                "Impostazioni salvate. Override DNS generato non trovato."
            )
            result["apply_failed"] = True
            return result

    compose = _compose_cmd()
    if changes.docker_dns and compose is None:
        result["dns_apply_message"] = (
            "Impostazioni salvate. Docker Compose non disponibile nel container API."
        )
        result["apply_failed"] = True
        return result

    if not _APPLY_LOCK.acquire(blocking=False):
        result["dns_apply_message"] = (
            "Impostazioni salvate. Applicazione già in corso, riprova tra poco."
        )
        result["apply_failed"] = True
        return result

    env = _docker_env()
    env["COMPOSE_PROJECT_NAME"] = _compose_project_name()
    messages: list[str] = []

    try:
        caddy_recreated = False
        if changes.docker_dns:
            assert compose is not None
            ok, msg, caddy_recreated = _apply_docker_dns_settings(compose, env)
            if not ok:
                result["dns_apply_message"] = f"Impostazioni salvate. {msg}"
                result["apply_failed"] = True
                return result
            result["dns_applied"] = True
            messages.append(msg)

        if changes.caddy_config:
            if caddy_recreated:
                ok, detail = validate_caddy_config()
                if not ok:
                    result["dns_applied"] = changes.docker_dns
                    result["dns_apply_message"] = (
                        "DNS applicati ma la nuova configurazione Caddy non è valida"
                        + (f": {detail}" if detail else ".")
                    )
                    result["apply_failed"] = True
                    return result
                result["caddy_reloaded"] = True
                messages.append("Caddy ricreato con il nuovo Caddyfile.")
            else:
                ok, detail = reload_caddy_config()
                if not ok:
                    prefix = " ".join(messages).strip()
                    result["dns_applied"] = changes.docker_dns
                    result["dns_apply_message"] = (
                        (f"{prefix} " if prefix else "Impostazioni salvate. ")
                        + "Ricarica Caddy fallita"
                        + (f": {detail}" if detail else ".")
                    )
                    result["apply_failed"] = True
                    return result
                result["caddy_reloaded"] = True
                messages.append("Caddy ricaricato con il nuovo Caddyfile.")

        if not messages:
            result["dns_apply_message"] = "Impostazioni salvate."
        elif changes.docker_dns and changes.caddy_config:
            result["dns_apply_message"] = " ".join(messages)
        elif changes.docker_dns:
            result["dns_apply_message"] = messages[0]
        else:
            result["dns_apply_message"] = messages[0]
        return result
    finally:
        _APPLY_LOCK.release()


def apply_docker_stack_settings() -> dict[str, Any]:
    """Backward-compatible wrapper: apply DNS and Caddy as if everything changed."""
    return apply_settings_changes(
        SettingsInfraChanges(public_url=True, acme_email=True, docker_dns=True)
    )
