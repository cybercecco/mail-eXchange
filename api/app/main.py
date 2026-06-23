import json
import sqlite3
import threading
import time
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.auth import (
    ChangePasswordRequest,
    LoginRequest,
    MfaConfirmRequest,
    MfaDisableRequest,
    MfaVerifyRequest,
    ProfileNotifyEmailUpdate,
    bootstrap_admin_if_needed,
    change_password,
    ensure_jwt_configured_for_protected_routes,
    get_current_user,
    login,
    mfa_confirm,
    mfa_disable,
    mfa_setup,
    require_admin,
    update_notify_email,
    verify_mfa,
)
from app.error_notifications import collect_error_lines, send_error_digest
from app.queue_ops import (
    QueueDeleteRequest,
    QueueFlushRequest,
    enqueue_delete,
    enqueue_flush,
    enqueue_hold_all,
    enqueue_postfix_pause,
    enqueue_postfix_resume,
    enqueue_release_all,
)
from app.db import init_db
from app.dns_check import check_all_domains, check_dns_for_domain
from app.domain_destinations import (
    DestinationCreate,
    DestinationUpdate,
    create_destination,
    delete_destination,
    list_destinations_for_domain,
    resolve_destination_for_mailbox,
    update_destination,
)
from app.domains import (
    DomainCreate,
    DomainUpdate,
    create_domain,
    delete_domain,
    get_domain,
    list_domains,
    assert_mailboxes_allowed,
    resolve_domain_for_mailbox,
    update_domain,
)
from app.mail_test import TestMailRequest, send_test_mail
from app.quarantine import (
    delete_quarantine,
    get_quarantine,
    list_quarantine,
    quarantine_worker_once,
    release_quarantine,
)
from app.service_restart import restart_daemon
from app.service_status import collect_daemon_status
from app.system_settings import (
    SystemSettingsUpdate,
    bootstrap_settings_from_env,
    get_settings,
    purge_legacy_cloudflare_token_from_db,
    settings_for_api,
    update_settings,
)
from app.mailbox_import import import_mailboxes_csv
from app.postfix_settings import PostfixSettings, get_settings as get_postfix_settings, persist_settings as persist_postfix_settings
from app.regenerate import regenerate_files
from app.spamassassin import SpamSettings, normalize_settings
from app.sync import (
    SyncDomainBundlePayload,
    SyncMailboxesPayload,
    apply_incoming_domain_sync,
    apply_incoming_mailbox_sync,
    attach_sync_warning,
    touch_domain_updated_at,
    verify_sync_auth,
)
from app.traffic_stats import collect_queue_listing, collect_traffic_stats, read_pipeline_snapshot
from app.relay_users import (
    RelayUserCreate,
    RelayUserUpdate,
    create_relay_user,
    delete_relay_user,
    list_relay_users,
    update_relay_user,
)
from app.users import UserCreate, UserUpdate, create_user, delete_user, list_users, update_user

app = FastAPI(title="Mail Exchange Control Plane")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

CurrentUser = Annotated[dict, Depends(get_current_user)]
AdminUser = Annotated[dict, Depends(require_admin)]


def _reject_sync_secret_for_non_admin(payload: DomainCreate | DomainUpdate, user: dict) -> None:
    if user.get("role") == "admin":
        return
    if isinstance(payload, DomainCreate):
        if payload.sync_secret:
            raise HTTPException(status_code=403, detail="Admin required for sync secret")
    elif "sync_secret" in payload.model_fields_set:
        raise HTTPException(status_code=403, detail="Admin required for sync secret")


class MailboxCreate(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    destination_host: str
    destination_port: int = 25
    enabled: bool = True
    domain_id: Optional[int] = None


class MailboxUpdate(BaseModel):
    email: Optional[str] = Field(default=None, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    destination_host: Optional[str] = None
    destination_port: Optional[int] = None
    enabled: Optional[bool] = None


def _error_digest_worker() -> None:
    time.sleep(120)
    while True:
        try:
            send_error_digest(force=False)
        except Exception:
            pass
        time.sleep(900)


def _quarantine_worker() -> None:
    time.sleep(30)
    while True:
        try:
            quarantine_worker_once()
        except Exception:
            pass
        time.sleep(60)


@app.on_event("startup")
def startup_event() -> None:
    init_db()
    bootstrap_admin_if_needed()
    ensure_jwt_configured_for_protected_routes()
    bootstrap_settings_from_env()
    purge_legacy_cloudflare_token_from_db()
    regenerate_files()
    threading.Thread(target=_error_digest_worker, daemon=True).start()
    threading.Thread(target=_quarantine_worker, daemon=True).start()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/system/daemons")
def api_system_daemons(_user: CurrentUser) -> dict:
    return collect_daemon_status()


@app.post("/api/system/daemons/{daemon_id}/restart")
def api_restart_daemon(daemon_id: str, _admin: AdminUser) -> dict:
    return restart_daemon(daemon_id)


@app.post("/api/auth/login")
def api_login(payload: LoginRequest) -> dict:
    return login(payload.username, payload.password)


@app.post("/api/auth/mfa/verify")
def api_mfa_verify(payload: MfaVerifyRequest) -> dict:
    return verify_mfa(payload.temp_token, payload.code)


@app.get("/api/auth/me")
def api_me(user: CurrentUser) -> dict:
    return user


@app.post("/api/auth/password")
def api_change_password(payload: ChangePasswordRequest, user: CurrentUser) -> dict:
    return change_password(user, payload.current_password, payload.new_password)


@app.put("/api/auth/profile/notify-email")
def api_update_notify_email(payload: ProfileNotifyEmailUpdate, user: CurrentUser) -> dict:
    result = update_notify_email(user, payload.notify_email)
    regenerate_files()
    return result


@app.post("/api/auth/mfa/setup")
def api_mfa_setup(user: CurrentUser) -> dict:
    return mfa_setup(user)


@app.post("/api/auth/mfa/confirm")
def api_mfa_confirm(payload: MfaConfirmRequest, user: CurrentUser) -> dict:
    return mfa_confirm(user, payload.code)


@app.post("/api/auth/mfa/disable")
def api_mfa_disable(payload: MfaDisableRequest, user: CurrentUser) -> dict:
    return mfa_disable(user, payload.password, payload.code)


@app.post("/api/auth/logout")
def api_logout() -> dict:
    return {"status": "logged_out"}


@app.get("/api/users")
def api_list_users(_admin: AdminUser) -> list[dict]:
    return list_users()


@app.post("/api/users")
def api_create_user(payload: UserCreate, _admin: AdminUser) -> dict:
    result = create_user(payload)
    regenerate_files()
    return result


@app.put("/api/users/{user_id}")
def api_update_user(user_id: int, payload: UserUpdate, admin: AdminUser) -> dict:
    result = update_user(user_id, payload, admin)
    regenerate_files()
    return result


@app.delete("/api/users/{user_id}")
def api_delete_user(user_id: int, admin: AdminUser) -> dict:
    result = delete_user(user_id, admin)
    regenerate_files()
    return result


@app.get("/api/relay-users")
def api_list_relay_users(_admin: AdminUser) -> list[dict]:
    return list_relay_users()


@app.post("/api/relay-users")
def api_create_relay_user(payload: RelayUserCreate, _admin: AdminUser) -> dict:
    result = create_relay_user(payload)
    regenerate_files()
    return result


@app.put("/api/relay-users/{user_id}")
def api_update_relay_user(user_id: int, payload: RelayUserUpdate, _admin: AdminUser) -> dict:
    result = update_relay_user(user_id, payload)
    regenerate_files()
    return result


@app.delete("/api/relay-users/{user_id}")
def api_delete_relay_user(user_id: int, _admin: AdminUser) -> dict:
    result = delete_relay_user(user_id)
    regenerate_files()
    return result


@app.get("/api/domains")
def api_list_domains(_user: CurrentUser) -> list[dict]:
    return list_domains()


@app.post("/api/domains")
def api_create_domain(payload: DomainCreate, user: CurrentUser) -> dict:
    _reject_sync_secret_for_non_admin(payload, user)
    result = create_domain(payload)
    regenerate_files()
    if result.get("sibling_fqdn"):
        result = attach_sync_warning(result, result["id"])
    return result


@app.put("/api/domains/{domain_id}")
def api_update_domain(domain_id: int, payload: DomainUpdate, user: CurrentUser) -> dict:
    _reject_sync_secret_for_non_admin(payload, user)
    row = get_domain(domain_id)
    result = update_domain(domain_id, payload)
    regenerate_files()
    sibling = result.get("sibling_fqdn") or row["sibling_fqdn"]
    should_push = bool(
        sibling
        and (
            ("sibling_fqdn" in payload.model_fields_set and result.get("sibling_fqdn"))
            or "dkim_selector" in payload.model_fields_set
        )
    )
    if should_push:
        result = attach_sync_warning(result, domain_id)
    return result


@app.post("/api/domains/{domain_id}/dkim/regenerate")
def api_regenerate_domain_dkim(domain_id: int, _user: CurrentUser) -> dict:
    from app.dkim_keys import regenerate_dkim_key_pair

    row = get_domain(domain_id)
    regenerate_dkim_key_pair(row["name"], row["dkim_selector"])
    regenerate_files()
    result = {"status": "regenerated"}
    if row["sibling_fqdn"]:
        result = attach_sync_warning(result, domain_id)
    return result


@app.delete("/api/domains/{domain_id}")
def api_delete_domain(domain_id: int, _user: CurrentUser) -> dict:
    result = delete_domain(domain_id)
    regenerate_files()
    return result


@app.get("/api/settings")
def api_get_settings(_admin: AdminUser) -> dict:
    from app.system_settings import docker_dns_servers_text, get_settings

    raw = get_settings()
    settings = settings_for_api(raw)
    settings["docker_dns_servers"] = docker_dns_servers_text(raw)
    return settings


@app.put("/api/settings")
def api_update_settings(payload: SystemSettingsUpdate, _admin: AdminUser) -> dict:
    from app.docker_compose_apply import SettingsInfraChanges, apply_settings_changes
    from app.system_settings import get_settings, persist_settings

    previous = get_settings()
    settings = update_settings(payload)
    current = get_settings()
    changes = SettingsInfraChanges(
        public_url=previous.get("public_url") != current.get("public_url"),
        acme_email=previous.get("acme_email") != current.get("acme_email"),
        docker_dns=previous.get("docker_dns") != current.get("docker_dns"),
    )

    regenerate_files()
    apply_result = apply_settings_changes(changes)
    if apply_result.get("apply_failed"):
        persist_settings(previous)
        regenerate_files()
        detail = apply_result.get("dns_apply_message") or "Applicazione impostazioni fallita"
        raise HTTPException(status_code=502, detail=detail)

    return {
        "status": "updated",
        "settings": settings,
        **apply_result,
    }


@app.post("/api/settings/test-mail")
def api_send_test_mail(payload: TestMailRequest, _admin: AdminUser) -> dict:
    return send_test_mail(payload)


@app.get("/api/domains/{domain_id}/destinations")
def api_list_destinations(domain_id: int, _user: CurrentUser) -> list[dict]:
    return list_destinations_for_domain(domain_id)


@app.post("/api/domains/{domain_id}/destinations")
def api_create_destination(
    domain_id: int, payload: DestinationCreate, _user: CurrentUser
) -> dict:
    result = create_destination(domain_id, payload)
    regenerate_files()
    return result


@app.put("/api/domains/{domain_id}/destinations/{destination_id}")
def api_update_destination(
    domain_id: int,
    destination_id: int,
    payload: DestinationUpdate,
    _user: CurrentUser,
) -> dict:
    result = update_destination(domain_id, destination_id, payload)
    regenerate_files()
    if result.get("mailboxes_updated", 0) > 0:
        return attach_sync_warning(result, domain_id)
    return result


@app.delete("/api/domains/{domain_id}/destinations/{destination_id}")
def api_delete_destination(
    domain_id: int, destination_id: int, _user: CurrentUser
) -> dict:
    result = delete_destination(domain_id, destination_id)
    regenerate_files()
    return result


@app.get("/api/mailboxes")
def list_mailboxes(_user: CurrentUser, domain_id: Optional[int] = None) -> list[dict]:
    from app.db import db

    query = """
        SELECT m.id, m.email, m.destination_host, m.destination_port, m.enabled,
               m.domain_id, d.name AS domain_name
        FROM mailboxes m
        LEFT JOIN domains d ON d.id = m.domain_id
    """
    params: tuple = ()
    if domain_id is not None:
        query += " WHERE m.domain_id = ?"
        params = (domain_id,)
    query += " ORDER BY m.email"
    with db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/mailboxes")
def create_mailbox(payload: MailboxCreate, _user: CurrentUser) -> dict:
    from app.db import db

    email = payload.email.lower()
    domain_id, _ = resolve_domain_for_mailbox(email, payload.domain_id)
    assert_mailboxes_allowed(domain_id)
    resolve_destination_for_mailbox(
        domain_id, payload.destination_host, payload.destination_port
    )
    with db() as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO mailboxes (email, destination_host, destination_port, enabled, domain_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    email,
                    payload.destination_host,
                    payload.destination_port,
                    int(payload.enabled),
                    domain_id,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Mailbox already exists") from exc
    touch_domain_updated_at(domain_id)
    regenerate_files()
    return attach_sync_warning({"id": cursor.lastrowid, "email": email}, domain_id)


@app.post("/api/mailboxes/import")
async def import_mailboxes(
    _user: CurrentUser,
    file: UploadFile = File(...),
    update_existing: bool = Form(False),
    skip_header: bool = Form(False),
) -> dict:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        csv_text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded") from exc
    result = import_mailboxes_csv(
        csv_text,
        update_existing=update_existing,
        skip_header=skip_header,
    )
    regenerate_files()
    warnings: list[str] = []
    for domain_id in result.get("affected_domain_ids", []):
        synced = attach_sync_warning({"status": "ok"}, domain_id)
        warning = synced.get("sync_warning")
        if warning and warning not in warnings:
            warnings.append(warning)
    if warnings:
        result["sync_warning"] = "; ".join(warnings)
    return result


@app.put("/api/mailboxes/{mailbox_id}")
def update_mailbox(mailbox_id: int, payload: MailboxUpdate, _user: CurrentUser) -> dict:
    from app.db import db

    with db() as conn:
        row = conn.execute("SELECT * FROM mailboxes WHERE id = ?", (mailbox_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Mailbox not found")
        email = row["email"]
        domain_id = row["domain_id"]
        previous_domain_id = domain_id
        assert_mailboxes_allowed(domain_id)
        if payload.email is not None:
            email = payload.email.strip().lower()
            domain_id, _ = resolve_domain_for_mailbox(email, None)
            assert_mailboxes_allowed(domain_id)
        destination_host = payload.destination_host or row["destination_host"]
        destination_port = payload.destination_port or row["destination_port"]
        resolve_destination_for_mailbox(domain_id, destination_host, destination_port)
        enabled = int(payload.enabled) if payload.enabled is not None else row["enabled"]
        try:
            conn.execute(
                """
                UPDATE mailboxes
                SET email = ?, domain_id = ?, destination_host = ?, destination_port = ?, enabled = ?
                WHERE id = ?
                """,
                (email, domain_id, destination_host, destination_port, enabled, mailbox_id),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Mailbox already exists") from exc
    touch_domain_updated_at(domain_id)
    if previous_domain_id and previous_domain_id != domain_id:
        touch_domain_updated_at(previous_domain_id)
    regenerate_files()
    result = attach_sync_warning({"status": "updated", "email": email}, domain_id)
    if previous_domain_id and previous_domain_id != domain_id:
        other = attach_sync_warning({"status": "updated"}, previous_domain_id)
        if other.get("sync_warning"):
            existing = result.get("sync_warning")
            extra = other["sync_warning"]
            result["sync_warning"] = f"{existing}; {extra}" if existing else extra
    return result


@app.delete("/api/mailboxes/{mailbox_id}")
def delete_mailbox(mailbox_id: int, _user: CurrentUser) -> dict:
    from app.db import db

    with db() as conn:
        row = conn.execute("SELECT domain_id FROM mailboxes WHERE id = ?", (mailbox_id,)).fetchone()
        domain_id = row["domain_id"] if row else None
        conn.execute("DELETE FROM mailboxes WHERE id = ?", (mailbox_id,))
        conn.commit()
    if domain_id is not None:
        touch_domain_updated_at(domain_id)
    regenerate_files()
    if domain_id is None:
        return {"status": "deleted"}
    return attach_sync_warning({"status": "deleted"}, domain_id)


@app.get("/api/spamassassin")
def get_spamassassin(_user: CurrentUser) -> dict:
    from app.db import db

    with db() as conn:
        raw_payload = conn.execute(
            "SELECT json_payload FROM spam_settings WHERE id = 1"
        ).fetchone()["json_payload"]
    return normalize_settings(json.loads(raw_payload))


@app.get("/api/dns/check")
def dns_check(_user: CurrentUser, domain: Optional[str] = Query(default=None)) -> dict:
    if domain:
        return check_dns_for_domain(domain)
    return check_all_domains()


@app.get("/api/stats/traffic")
def api_traffic_stats(
    _user: CurrentUser,
    window_minutes: int = Query(default=60, ge=5, le=1440),
) -> dict:
    return collect_traffic_stats(window_minutes=window_minutes)


@app.get("/api/stats/queue")
def api_queue_listing(
    _user: CurrentUser,
    type: str = Query(default="active"),
    window_minutes: int = Query(default=60, ge=5, le=1440),
) -> dict:
    return collect_queue_listing(queue_type=type, window_minutes=window_minutes)


@app.post("/api/stats/queue/flush")
def api_queue_flush(payload: QueueFlushRequest, _admin: AdminUser) -> dict:
    return enqueue_flush(payload)


@app.post("/api/stats/queue/delete")
def api_queue_delete(payload: QueueDeleteRequest, _admin: AdminUser) -> dict:
    return enqueue_delete(payload)


@app.get("/api/stats/queue/snapshot")
def api_queue_snapshot(_user: CurrentUser) -> dict:
    return read_pipeline_snapshot()


@app.post("/api/stats/queue/hold")
def api_queue_hold(_admin: AdminUser) -> dict:
    return enqueue_hold_all()


@app.post("/api/stats/queue/release")
def api_queue_release(_admin: AdminUser) -> dict:
    return enqueue_release_all()


@app.post("/api/stats/queue/pause")
def api_queue_pause(_admin: AdminUser) -> dict:
    return enqueue_postfix_pause()


@app.post("/api/stats/queue/resume")
def api_queue_resume(_admin: AdminUser) -> dict:
    return enqueue_postfix_resume()


@app.get("/api/notifications/errors/preview")
def api_errors_preview(_admin: AdminUser) -> dict:
    from app.error_notifications import ERROR_WINDOW_MINUTES

    entries = collect_error_lines()
    return {
        "count": len(entries),
        "entries": entries,
        "window_minutes": ERROR_WINDOW_MINUTES,
    }


@app.post("/api/notifications/errors/send")
def api_errors_send(_admin: AdminUser, force: bool = Query(default=False)) -> dict:
    return send_error_digest(force=force)


@app.post("/api/sync/mailboxes")
def api_sync_mailboxes(
    payload: SyncMailboxesPayload,
    authorization: Annotated[Optional[str], Header(alias="Authorization")] = None,
) -> dict:
    verify_sync_auth(payload.domain_name, authorization)
    return apply_incoming_mailbox_sync(payload)


@app.post("/api/sync/domain-bundle")
def api_sync_domain_bundle(
    payload: SyncDomainBundlePayload,
    authorization: Annotated[Optional[str], Header(alias="Authorization")] = None,
) -> dict:
    verify_sync_auth(payload.domain_name, authorization)
    return apply_incoming_domain_sync(payload)


@app.put("/api/spamassassin")
def set_spamassassin(payload: SpamSettings, _user: CurrentUser) -> dict:
    from app.db import db

    normalized = normalize_settings(payload.model_dump())
    with db() as conn:
        conn.execute(
            "UPDATE spam_settings SET json_payload = ? WHERE id = 1",
            (json.dumps(normalized),),
        )
        conn.commit()
    regenerate_files()
    return {"status": "updated", "settings": normalized}


@app.get("/api/postfix")
def get_postfix(_user: CurrentUser) -> dict:
    return get_postfix_settings()


@app.put("/api/postfix")
def set_postfix(payload: PostfixSettings, _user: CurrentUser) -> dict:
    normalized = persist_postfix_settings(payload.model_dump())
    regenerate_files()
    return {"status": "updated", "settings": normalized}


@app.get("/api/quarantine")
def api_quarantine_list(
    _admin: AdminUser,
    from_addr: str = Query(default="", alias="from"),
    to: str = Query(default=""),
    q: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    return list_quarantine(from_filter=from_addr, to_filter=to, query=q, limit=limit)


@app.get("/api/quarantine/{entry_id}")
def api_quarantine_detail(entry_id: str, _admin: AdminUser) -> dict:
    return get_quarantine(entry_id)


@app.post("/api/quarantine/{entry_id}/release")
def api_quarantine_release(entry_id: str, _admin: AdminUser) -> dict:
    return release_quarantine(entry_id)


@app.delete("/api/quarantine/{entry_id}")
def api_quarantine_delete(entry_id: str, _admin: AdminUser) -> dict:
    return delete_quarantine(entry_id)
