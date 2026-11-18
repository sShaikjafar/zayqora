"""Email notifications. Every message is stored in the `notifications` table first.
It is then sent in the background if SMTP_HOST is configured; a failed send never
breaks the order or reservation that triggered it (spec 15.3: reliability rules)."""
import logging
import smtplib
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal
from .models import MANAGER, OWNER, Notification, User, utcnow

log = logging.getLogger("zayqora.mailer")


def queue_notification(db: Session, restaurant_id: int, kind: str, recipient: str, subject: str, body: str) -> int:
    row = Notification(
        restaurant_id=restaurant_id, kind=kind, recipient=recipient,
        subject=subject[:200], body=body, status="PENDING",
    )
    db.add(row)
    db.commit()
    return row.id


def queue_staff_notifications(db: Session, restaurant_id: int, kind: str, subject: str, body: str) -> list[int]:
    emails = db.scalars(
        select(User.email).where(
            User.restaurant_id == restaurant_id, User.is_active.is_(True), User.role.in_([OWNER, MANAGER])
        )
    ).all()
    return [queue_notification(db, restaurant_id, kind, e, subject, body) for e in emails]


def _send_smtp(recipient: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


def deliver(notification_ids: list[int]) -> None:
    """Runs after the HTTP response has been sent (FastAPI background task)."""
    if not notification_ids:
        return
    with SessionLocal() as db:
        for nid in notification_ids:
            n = db.get(Notification, nid)
            if n is None:
                continue
            if not settings.smtp_host:
                n.status = "LOGGED"  # no mail server configured: kept in the log only
            else:
                try:
                    _send_smtp(n.recipient, n.subject, n.body)
                    n.status, n.sent_at = "SENT", utcnow()
                except Exception as exc:  # never let email problems escape
                    log.warning("Email to %s failed: %s", n.recipient, exc)
                    n.status, n.error = "FAILED", str(exc)[:255]
        db.commit()
