"""Enqueue Postfix queue actions for mx-postfix to execute via shared volume."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.db import DATA_DIR

STATS_DIR = DATA_DIR / "stats"
QUEUE_COMMAND = STATS_DIR / "queue_command.json"
QUEUE_ID_PATTERN = re.compile(r"^[0-9A-F]{6,16}$", re.I)
VALID_QUEUE_TYPES = {"active", "deferred", "hold", "all"}


class QueueFlushRequest(BaseModel):
    queue_type: str = Field(
        default="deferred",
        description="deferred (default), active, hold, or all",
    )


class QueueDeleteRequest(BaseModel):
    queue_ids: list[str] = Field(default_factory=list)
    delete_all: bool = False
    queue_type: str = Field(default="all")


class QueuePauseRequest(BaseModel):
    """Optional body for pause/resume endpoints (reserved for future options)."""

    confirm: bool = True


def _normalize_queue_type(value: str) -> str:
    normalized = (value or "all").strip().lower()
    if normalized not in VALID_QUEUE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"queue_type must be one of: {', '.join(sorted(VALID_QUEUE_TYPES))}",
        )
    return normalized


def _validate_queue_ids(queue_ids: list[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in queue_ids:
        qid = (raw or "").strip().upper()
        if not qid:
            continue
        if not QUEUE_ID_PATTERN.match(qid):
            raise HTTPException(status_code=400, detail=f"Invalid queue id: {raw}")
        cleaned.append(qid)
    return cleaned


def _write_command(payload: dict[str, Any]) -> dict[str, Any]:
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    if QUEUE_COMMAND.is_file():
        try:
            pending = json.loads(QUEUE_COMMAND.read_text(encoding="utf-8"))
            if pending.get("status") == "pending":
                raise HTTPException(
                    status_code=409,
                    detail="Another queue operation is already pending",
                )
        except json.JSONDecodeError:
            pass

    payload["status"] = "pending"
    payload["requested_at"] = datetime.now(timezone.utc).isoformat()
    tmp = QUEUE_COMMAND.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(QUEUE_COMMAND)
    return payload


def enqueue_flush(payload: QueueFlushRequest) -> dict[str, Any]:
    queue_type = _normalize_queue_type(payload.queue_type)
    if queue_type == "all":
        queue_type = "deferred"
    command = _write_command({"action": "flush", "queue_type": queue_type})
    return {
        "status": "queued",
        "action": "flush",
        "queue_type": queue_type,
        "requested_at": command["requested_at"],
    }


def enqueue_delete(payload: QueueDeleteRequest) -> dict[str, Any]:
    queue_type = _normalize_queue_type(payload.queue_type)
    queue_ids = _validate_queue_ids(payload.queue_ids)

    if not payload.delete_all and not queue_ids:
        raise HTTPException(
            status_code=400,
            detail="Provide queue_ids or set delete_all=true",
        )

    command = _write_command(
        {
            "action": "delete",
            "queue_type": queue_type,
            "delete_all": bool(payload.delete_all),
            "queue_ids": queue_ids,
        }
    )
    return {
        "status": "queued",
        "action": "delete",
        "queue_type": queue_type,
        "delete_all": bool(payload.delete_all),
        "queue_ids": queue_ids,
        "requested_at": command["requested_at"],
    }


def enqueue_hold_all(_payload: QueuePauseRequest | None = None) -> dict[str, Any]:
    command = _write_command({"action": "hold_all"})
    return {
        "status": "queued",
        "action": "hold_all",
        "requested_at": command["requested_at"],
    }


def enqueue_release_all(_payload: QueuePauseRequest | None = None) -> dict[str, Any]:
    command = _write_command({"action": "release_all"})
    return {
        "status": "queued",
        "action": "release_all",
        "requested_at": command["requested_at"],
    }


def enqueue_postfix_pause(_payload: QueuePauseRequest | None = None) -> dict[str, Any]:
    command = _write_command({"action": "postfix_pause"})
    return {
        "status": "queued",
        "action": "postfix_pause",
        "requested_at": command["requested_at"],
    }


def enqueue_postfix_resume(_payload: QueuePauseRequest | None = None) -> dict[str, Any]:
    command = _write_command({"action": "postfix_resume"})
    return {
        "status": "queued",
        "action": "postfix_resume",
        "requested_at": command["requested_at"],
    }


def read_last_command_result() -> dict[str, Any] | None:
    if not QUEUE_COMMAND.is_file():
        return None
    try:
        data = json.loads(QUEUE_COMMAND.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("status") in {"done", "error"}:
        return data
    return None
