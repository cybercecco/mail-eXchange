import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.db import db
from app.dns_check import check_dns_for_domain
from app.domains import get_domain

POSTFIX_SMTP_HOST = os.environ.get("POSTFIX_SMTP_HOST", "postfix")
POSTFIX_SMTP_PORT = int(os.environ.get("POSTFIX_SMTP_PORT", "25"))
POSTFIX_SMTP_TIMEOUT = int(os.environ.get("POSTFIX_SMTP_TIMEOUT", "30"))
POSTFIX_SMTP_TLS = os.environ.get("POSTFIX_SMTP_TLS", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def prepare_smtp_session(smtp: smtplib.SMTP) -> None:
    """EHLO and optional STARTTLS before submitting to Postfix."""
    smtp.ehlo()
    if not POSTFIX_SMTP_TLS:
        return
    if smtp.has_extn("starttls"):
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()


class TestMailRequest(BaseModel):
    domain_id: int
    mailbox_id: int
    subject: str = Field(default="Mail Exchange — test invio", max_length=200)


def _get_mailbox_for_domain(domain_id: int, mailbox_id: int) -> dict:
    get_domain(domain_id)
    with db() as conn:
        row = conn.execute(
            """
            SELECT m.id, m.email, m.enabled, m.destination_host, m.destination_port,
                   d.name AS domain_name, d.enabled AS domain_enabled, d.dkim_selector
            FROM mailboxes m
            INNER JOIN domains d ON d.id = m.domain_id
            WHERE m.id = ? AND m.domain_id = ?
            """,
            (mailbox_id, domain_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Mailbox not found for this domain")
    if not row["domain_enabled"]:
        raise HTTPException(status_code=400, detail="Domain is disabled")
    if not row["enabled"]:
        raise HTTPException(status_code=400, detail="Mailbox is disabled")
    return dict(row)


def _format_exception(exc: BaseException) -> str:
    lines = [f"{type(exc).__name__}: {exc}"]
    if isinstance(exc, smtplib.SMTPResponseException):
        code = getattr(exc, "smtp_code", None)
        err = getattr(exc, "smtp_error", b"")
        if isinstance(err, bytes):
            err = err.decode("utf-8", errors="replace")
        lines.append(f"SMTP code: {code}")
        lines.append(f"SMTP response: {err}")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        for addr, refused in exc.recipients.items():
            detail = refused
            if isinstance(refused, tuple) and len(refused) >= 2:
                code, resp = refused[0], refused[1]
                if isinstance(resp, bytes):
                    resp = resp.decode("utf-8", errors="replace")
                detail = f"code={code}, response={resp}"
            lines.append(f"Recipient {addr}: {detail}")
    if isinstance(exc, smtplib.SMTPSenderRefused):
        code = getattr(exc, "smtp_code", None)
        resp = getattr(exc, "smtp_error", b"")
        if isinstance(resp, bytes):
            resp = resp.decode("utf-8", errors="replace")
        lines.append(f"Sender refused: code={code}, response={resp}")
    if isinstance(exc, smtplib.SMTPDataError):
        code = getattr(exc, "smtp_code", None)
        resp = getattr(exc, "smtp_error", b"")
        if isinstance(resp, bytes):
            resp = resp.decode("utf-8", errors="replace")
        lines.append(f"DATA error: code={code}, response={resp}")
    lines.append(f"Host: {POSTFIX_SMTP_HOST}:{POSTFIX_SMTP_PORT}")
    return "\n".join(lines)


def _dns_overall_status(dns_check: dict) -> str:
    statuses = [
        dns_check.get("spf", {}).get("status"),
        dns_check.get("dkim", {}).get("status"),
        dns_check.get("dmarc", {}).get("status"),
    ]
    if "error" in statuses:
        return "error"
    if "warning" in statuses:
        return "warning"
    return "ok"


def send_test_mail(payload: TestMailRequest) -> dict:
    mailbox = _get_mailbox_for_domain(payload.domain_id, payload.mailbox_id)
    recipient = mailbox["email"].strip().lower()
    domain_name = mailbox["domain_name"]
    dkim_selector = mailbox.get("dkim_selector") or "mail"
    dns_check = check_dns_for_domain(domain_name, dkim_selector)
    dns_overall = _dns_overall_status(dns_check)

    sender = f"mx-test@{domain_name}"
    subject = payload.subject.strip() or "Mail Exchange — test invio"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    body = (
        f"Messaggio di test generato da Mail Exchange.\n\n"
        f"Destinatario: {recipient}\n"
        f"Routing configurato: {mailbox['destination_host']}:{mailbox['destination_port']}\n"
        f"Timestamp: {now}\n"
    )

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    smtp_error: str | None = None
    smtp_result: dict | None = None
    try:
        with smtplib.SMTP(POSTFIX_SMTP_HOST, POSTFIX_SMTP_PORT, timeout=POSTFIX_SMTP_TIMEOUT) as smtp:
            prepare_smtp_session(smtp)
            smtp.send_message(message)
        smtp_result = {
            "status": "queued",
            "from": sender,
            "to": recipient,
            "destination": f"{mailbox['destination_host']}:{mailbox['destination_port']}",
            "smtp_host": POSTFIX_SMTP_HOST,
            "smtp_port": POSTFIX_SMTP_PORT,
        }
    except (OSError, smtplib.SMTPException, Exception) as exc:
        smtp_error = _format_exception(exc)

    overall = "ok"
    if smtp_error:
        overall = "smtp_failed"
    elif dns_overall != "ok":
        overall = "dns_issues"
    elif smtp_result:
        overall = "ok"

    return {
        "status": overall,
        "dns_check": dns_check,
        "dns_overall": dns_overall,
        "smtp": smtp_result,
        "smtp_error": smtp_error,
    }
