"""Transactional email via an outbox table.

Every send is persisted first, then dispatched. Failures stay visible to
admins (Admin → Email) and can be retried without creating duplicate
invitations — retries re-send the same EmailMessage row.

Providers:
- resend   — Resend HTTP API (RESEND_API_KEY)
- console  — DEV ADAPTER: prints the email to the server log, marks it sent.
             Combined with EMAIL_DISABLE_SENDS=true nothing ever leaves the box.
"""
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session

from .config import get_settings
from .models import EmailMessage

log = logging.getLogger("exponential.email")


def queue_email(
    db: Session,
    kind: str,
    to: str,
    subject: str,
    body_text: str,
    body_html: str = "",
    invitation_id: str | None = None,
    send_now: bool = True,
) -> EmailMessage:
    msg = EmailMessage(
        kind=kind,
        to_email=to,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        invitation_id=invitation_id,
    )
    db.add(msg)
    db.flush()
    if send_now:
        dispatch(db, msg)
    return msg


def dispatch(db: Session, msg: EmailMessage) -> EmailMessage:
    s = get_settings()
    msg.attempts += 1
    if s.email_disable_sends:
        msg.status = "skipped"
        msg.error = "EMAIL_DISABLE_SENDS is set — intentionally not sent"
        db.flush()
        return msg
    try:
        if s.email_provider == "resend":
            _send_resend(msg)
        else:  # console dev adapter
            log.info(
                "EMAIL (console adapter) to=%s subject=%s\n%s",
                msg.to_email, msg.subject, msg.body_text,
            )
        msg.status = "sent"
        msg.error = ""
        msg.sent_at = datetime.now(UTC)
    except Exception as exc:  # keep failure visible, never crash the request
        log.exception("email send failed id=%s to=%s", msg.id, msg.to_email)
        msg.status = "failed"
        msg.error = str(exc)[:2000]
    db.flush()
    return msg


def retry(db: Session, msg: EmailMessage) -> EmailMessage:
    msg.status = "pending"
    db.flush()
    return dispatch(db, msg)


def _send_resend(msg: EmailMessage) -> None:
    s = get_settings()
    if not s.resend_api_key:
        raise RuntimeError("RESEND_API_KEY is not configured")
    payload: dict = {
        "from": s.email_from,
        "to": [msg.to_email],
        "subject": msg.subject,
        "text": msg.body_text,
    }
    if msg.body_html:
        payload["html"] = msg.body_html
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {s.resend_api_key}"},
        json=payload,
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Resend {resp.status_code}: {resp.text[:500]}")
