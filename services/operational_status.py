"""Persist a bounded history of operational outcomes for staff visibility."""
import json
import logging
import sqlite3
from pathlib import Path

from services.db import get_db
from services.operational_logging import log_operational_event
from services.timezone_service import current_timestamp, timestamp_days_ago

logger = logging.getLogger(__name__)
OPERATIONAL_EVENT_RETENTION_DAYS = 180
OPERATIONAL_EVENT_MAX_ROWS = 2000


def migrate_operational_events_schema(conn):
    """Create the operational event journal and its time index."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS operational_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_operational_events_type_time "
        "ON operational_events (event_type, occurred_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_operational_events_time "
        "ON operational_events (occurred_at DESC)"
    )


def record_operational_event(event_type, details=None, conn=None, db_path=None):
    """Record a sanitized operational outcome, retaining only recent bounded history."""
    owns_connection = conn is None
    if conn is None:
        conn = (
            sqlite3.connect(Path(db_path))
            if db_path is not None
            else get_db()
        )
        conn.row_factory = sqlite3.Row

    try:
        migrate_operational_events_schema(conn)
        encoded_details = json.dumps(
            details or {}, ensure_ascii=False, sort_keys=True
        )
        conn.execute(
            """
            INSERT INTO operational_events (event_type, occurred_at, details)
            VALUES (?, ?, ?)
            """,
            (event_type, current_timestamp(), encoded_details),
        )
        conn.execute(
            """
            DELETE FROM operational_events
            WHERE id NOT IN (
                SELECT id
                FROM operational_events
                ORDER BY occurred_at DESC, id DESC
                LIMIT ?
            )
            """,
            (OPERATIONAL_EVENT_MAX_ROWS,),
        )
        conn.execute(
            "DELETE FROM operational_events WHERE occurred_at < ?",
            (timestamp_days_ago(OPERATIONAL_EVENT_RETENTION_DAYS),),
        )
        if owns_connection:
            conn.commit()
    finally:
        if owns_connection:
            conn.close()


def _latest_event(conn, event_type):
    row = conn.execute(
        """
        SELECT event_type, occurred_at, details
        FROM operational_events
        WHERE event_type = ?
        ORDER BY occurred_at DESC, id DESC
        LIMIT 1
        """,
        (event_type,),
    ).fetchone()
    if row is None:
        return None
    return {
        "event_type": row[0],
        "occurred_at": row[1],
        "details": json.loads(row[2]),
    }


def get_operational_dashboard(conn):
    """Return the small set of health facts shown on the administrator backup page."""
    migrate_operational_events_schema(conn)
    latest = {
        "rating_replay_success": _latest_event(conn, "rating_replay_succeeded"),
        "rating_replay_failure": _latest_event(conn, "rating_replay_failed"),
        "manual_backup_success": _latest_event(conn, "manual_backup_succeeded"),
        "scheduled_backup_success": _latest_event(conn, "scheduled_backup_succeeded"),
        "scheduled_backup_failure": _latest_event(conn, "scheduled_backup_failed"),
        "restore_check_success": _latest_event(conn, "restore_check_succeeded"),
        "restore_check_failure": _latest_event(conn, "restore_check_failed"),
        "retention_failure": _latest_event(conn, "backup_retention_failed"),
        "backup_restore_success": _latest_event(conn, "backup_restore_succeeded"),
        "backup_restore_failure": _latest_event(conn, "backup_restore_failed"),
        "backup_action_failure": _latest_event(conn, "backup_action_failed"),
    }
    rating_state_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'rating_state'"
    ).fetchone()
    dirty_row = (
        conn.execute(
            "SELECT earliest_dirty_date FROM rating_state WHERE id = 1"
        ).fetchone()
        if rating_state_exists
        else None
    )
    latest["rating_status_known"] = dirty_row is not None
    latest["earliest_dirty_date"] = (
        dirty_row[0] if dirty_row else None
    )

    latest["failed_admin_actions"] = []
    for row in conn.execute(
        """
        SELECT event_type, occurred_at, details
        FROM operational_events
        WHERE event_type IN ('admin_action_failed', 'backup_action_failed')
        ORDER BY occurred_at DESC, id DESC
        LIMIT 10
        """
    ).fetchall():
        latest["failed_admin_actions"].append(
            {
                "event_type": row[0],
                "occurred_at": row[1],
                "details": json.loads(row[2]),
            }
        )
    return latest


def record_operational_event_safely(
    event_type, details=None, conn=None, db_path=None
):
    """Record status without masking the operation's primary result."""
    try:
        record_operational_event(
            event_type, details, conn=conn, db_path=db_path
        )
        return True
    except sqlite3.Error:
        log_operational_event(
            logger,
            logging.ERROR,
            "operations.status_persist_failed",
            "Could not persist operational status.",
            event_type=event_type,
            exc_info=True,
        )
        return False
