"""Operational email alerts and repeated login-limit event tracking."""
import hashlib
import hmac
from email.message import EmailMessage
import logging
import os
import smtplib
import time

from config import (
    MAIL_FROM,
    MAIL_PASSWORD,
    MAIL_PORT,
    MAIL_SERVER,
    MAIL_USERNAME,
    MAIL_USE_TLS,
)
from services.db import get_db
from services.operational_logging import log_operational_event

logger = logging.getLogger(__name__)
LOGIN_ALERT_LOOKBACK_SECONDS = 30 * 24 * 60 * 60
_RECIPIENT_CACHE_TTL_SECONDS = 60
_admin_recipients = ()
_admin_recipients_refreshed_at = None


def ensure_login_alert_events_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS login_alert_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL CHECK(source_type IN ('ip', 'username')),
            source_hash TEXT NOT NULL,
            occurred_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_login_alert_events_source_time
        ON login_alert_events (source_type, source_hash, occurred_at)
        """
    )


def record_login_threshold_events(
        conn, sources, now, login_window_seconds):
    """Record once per limiter window and report sources repeatedly limited in 30 days."""
    ensure_login_alert_events_table(conn)
    lookback = now - LOGIN_ALERT_LOOKBACK_SECONDS
    conn.execute(
        "DELETE FROM login_alert_events WHERE occurred_at < ?",
        (lookback,),
    )
    repeated_sources = []

    for source_type, value in sources:
        if not value:
            continue
        source_hash = hmac.new(
            os.environ["APP_SECRET_KEY"].encode("utf-8"),
            f"{source_type}:{value}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        current_window = conn.execute(
            """
            SELECT 1 FROM login_alert_events
            WHERE source_type = ? AND source_hash = ? AND occurred_at >= ?
            LIMIT 1
            """,
            (source_type, source_hash, now - login_window_seconds),
        ).fetchone()
        if current_window:
            continue

        conn.execute(
            """
            INSERT INTO login_alert_events (source_type, source_hash, occurred_at)
            VALUES (?, ?, ?)
            """,
            (source_type, source_hash, now),
        )
        occurrence_count = conn.execute(
            """
            SELECT COUNT(*) FROM login_alert_events
            WHERE source_type = ? AND source_hash = ? AND occurred_at >= ?
            """,
            (source_type, source_hash, lookback),
        ).fetchone()[0]
        log_operational_event(
            logger,
            logging.WARNING,
            "security.login_rate_limit.reached",
            "Login rate-limit threshold reached.",
            source_type=source_type,
            occurrences_in_30_days=occurrence_count,
        )
        if occurrence_count > 1:
            repeated_sources.append({
                "source_type": source_type,
                "occurrences_in_30_days": occurrence_count,
            })
    return repeated_sources


def _load_active_admin_recipients():
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT u.email
            FROM users u
            JOIN user_roles ur ON ur.user_id = u.id
            JOIN roles r ON r.id = ur.role_id
            WHERE u.is_active = 1
              AND r.name = 'administrator'
              AND TRIM(COALESCE(u.email, '')) <> ''
            """
        ).fetchall()
    finally:
        conn.close()

    recipients = {}
    for row in rows:
        address = str(row[0]).strip()
        local_part, separator, domain = address.partition("@")
        if (
            separator
            and local_part
            and domain
            and "@" not in domain
            and not any(character.isspace() for character in address)
        ):
            recipients.setdefault(address.casefold(), address)
    return tuple(sorted(recipients.values(), key=str.casefold))


def refresh_admin_alert_recipients():
    global _admin_recipients, _admin_recipients_refreshed_at
    _admin_recipients = _load_active_admin_recipients()
    _admin_recipients_refreshed_at = time.monotonic()
    return _admin_recipients


def prime_admin_alert_recipients():
    try:
        refresh_admin_alert_recipients()
        return True
    except Exception:
        log_operational_event(
            logger,
            logging.ERROR,
            "alerts.recipient_refresh.failed",
            "Could not load administrator alert recipients.",
            exc_info=True,
        )
        return False


def _get_admin_alert_recipients():
    if (
        _admin_recipients_refreshed_at is not None
        and time.monotonic() - _admin_recipients_refreshed_at
        < _RECIPIENT_CACHE_TTL_SECONDS
    ):
        return _admin_recipients
    try:
        return refresh_admin_alert_recipients()
    except Exception:
        log_operational_event(
            logger,
            logging.ERROR,
            "alerts.recipient_refresh.failed",
            "Could not refresh administrator alert recipients; using the cached list.",
            exc_info=True,
        )
        return _admin_recipients


def send_admin_alert(event_name, summary, details=None):
    """Email active administrators; report delivery failures without masking the source event."""
    recipients = _get_admin_alert_recipients()
    if not MAIL_SERVER:
        log_operational_event(
            logger,
            logging.ERROR,
            "alerts.delivery.unavailable",
            "Operational alert could not be sent because SMTP is not configured.",
            alert_type=event_name,
        )
        return False
    if not recipients:
        log_operational_event(
            logger,
            logging.ERROR,
            "alerts.delivery.unavailable",
            "Operational alert could not be sent because no active administrator has an email address.",
            alert_type=event_name,
        )
        return False

    try:
        message = EmailMessage()
        message["Subject"] = f"Glicko DB alert: {event_name.replace('_', ' ')}"
        message["From"] = MAIL_FROM or MAIL_USERNAME or "no-reply@localhost"
        message["To"] = ", ".join(recipients)
        message.set_content(
            f"{summary}\n\n"
            f"Event: {event_name}\n"
            f"Details: {details or 'Review the application logs for details.'}\n"
        )
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=10) as smtp:
            if MAIL_USE_TLS:
                smtp.starttls()
            if MAIL_USERNAME:
                smtp.login(MAIL_USERNAME, MAIL_PASSWORD)
            smtp.send_message(message)
    except Exception:
        log_operational_event(
            logger,
            logging.ERROR,
            "alerts.delivery.failed",
            "Operational alert email delivery failed.",
            exc_info=True,
            alert_type=event_name,
            recipient_count=len(recipients),
        )
        return False

    log_operational_event(
        logger,
        logging.INFO,
        "alerts.delivery.completed",
        "Operational alert email sent.",
        alert_type=event_name,
        recipient_count=len(recipients),
    )
    return True
