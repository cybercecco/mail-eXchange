"""Restart Docker stack services via docker CLI (admin-only)."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from typing import Any

from fastapi import HTTPException

from app.service_status import get_daemon_defn

_RESTART_LOCKS: dict[str, threading.Lock] = {}
_RESTART_LOCKS_GUARD = threading.Lock()
_RESTART_TIMEOUT = float(os.environ.get("DAEMON_RESTART_TIMEOUT", "120"))


def _docker_socket_path() -> str:
    return os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")


def docker_restart_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return os.path.exists(_docker_socket_path())


def _lock_for(daemon_id: str) -> threading.Lock:
    with _RESTART_LOCKS_GUARD:
        if daemon_id not in _RESTART_LOCKS:
            _RESTART_LOCKS[daemon_id] = threading.Lock()
        return _RESTART_LOCKS[daemon_id]


def restart_daemon(daemon_id: str) -> dict[str, Any]:
    defn = get_daemon_defn(daemon_id)
    if defn is None:
        raise HTTPException(status_code=404, detail="Servizio sconosciuto")

    container = defn.get("container")
    if not defn.get("restartable") or not container:
        raise HTTPException(status_code=400, detail="Servizio non riavviabile")

    if not docker_restart_available():
        raise HTTPException(
            status_code=503,
            detail="Docker CLI o socket non disponibili per il riavvio",
        )

    lock = _lock_for(daemon_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail={
                "status": "error",
                "message": "Riavvio già in corso per questo servizio",
                "daemon_id": daemon_id,
                "container": container,
            },
        )

    response: dict[str, Any] = {
        "daemon_id": daemon_id,
        "container": container,
        "status": "queued",
    }
    if daemon_id == "api":
        response["warning"] = (
            "Riavvio dell'API: la sessione corrente potrebbe interrompersi."
        )

    env = os.environ.copy()
    env["DOCKER_HOST"] = f"unix://{_docker_socket_path()}"

    try:
        proc = subprocess.run(
            ["docker", "restart", container],
            capture_output=True,
            text=True,
            timeout=_RESTART_TIMEOUT,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        response["status"] = "error"
        response["message"] = f"Timeout riavvio container ({exc.timeout}s)"
        return response
    finally:
        lock.release()

    if proc.returncode != 0:
        stderr = (proc.stderr or proc.stdout or "").strip()
        response["status"] = "error"
        response["message"] = stderr or f"docker restart fallito (codice {proc.returncode})"
        return response

    response["status"] = "success"
    response["message"] = (proc.stdout or "").strip() or f"Container {container} riavviato"
    return response
