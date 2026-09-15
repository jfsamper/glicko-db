"""Owns the admin audit log schema and write path."""
import json
import os

from flask import session

from services.db import get_db
from services.timezone_service import current_timestamp, timestamp_days_ago

AUDIT_RETENTION_DAYS = max(
    1, int(os.environ.get("AUDIT_RETENTION_DAYS", "730")))
AUDIT_DETAILS_MAX_BYTES = 2048


def migrate_audit_log_schema(conn):
    """Create the admin audit log table and indexes if they are missing."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action_type TEXT NOT NULL,
            resource_type TEXT,
            details TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_log_user_time ON audit_log (user_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_log_action_time ON audit_log (action_type, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_log_time ON audit_log (created_at DESC)"
    )
    conn.execute(
        "DELETE FROM audit_log WHERE created_at < ?",
        (timestamp_days_ago(AUDIT_RETENTION_DAYS),),
    )
    conn.commit()


def log_admin_action(action_type, resource_type=None, details=None, user_id=None, conn=None):
    """Persist an authenticated admin action for audit review."""
    owns_connection = conn is None
    conn = conn or get_db()

    try:
        migrate_audit_log_schema(conn)

        if user_id is None:
            try:
                user_id = session.get("user_id")
            except RuntimeError:
                user_id = None

        if details is None:
            encoded_details = "{}"
        elif isinstance(details, (dict, list, tuple)):
            encoded_details = json.dumps(
                details, ensure_ascii=False, sort_keys=True, default=str)
        else:
            encoded_details = json.dumps(str(details), ensure_ascii=False)

        encoded_details = encoded_details.encode("utf-8")[:AUDIT_DETAILS_MAX_BYTES].decode(
            "utf-8", errors="ignore"
        )

        conn.execute(
            """
            INSERT INTO audit_log (user_id, action_type, resource_type, details, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, action_type, resource_type,
             encoded_details, current_timestamp()),
        )
        if owns_connection:
            conn.commit()
    finally:
        if owns_connection:
            conn.close()
