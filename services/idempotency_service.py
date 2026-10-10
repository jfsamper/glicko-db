"""Server-side idempotency keys for non-repeatable tournament commands.

A key is claimed before its command runs and records the stored outcome once the
command finishes. A repeated key replays that outcome instead of running the command
again. If the first attempt never completed (for example, the process crashed), the
key stays ``in_progress`` and later attempts are refused, because the outcome is unknown.
"""
import re
import sqlite3
import uuid

from services.timezone_service import current_timestamp, timestamp_days_ago

KEY_PATTERN = re.compile(r"[A-Za-z0-9._-]{8,128}")
RETENTION_DAYS = 90


class IdempotencyError(Exception):
    pass


class InvalidIdempotencyKey(IdempotencyError):
    pass


class IdempotencyConflict(IdempotencyError):
    """The key was already used for a different command or tournament."""


class IdempotencyInProgress(IdempotencyError):
    """The key's first attempt has not completed, so its outcome is unknown."""


def new_idempotency_key():
    return uuid.uuid4().hex


def migrate_idempotency_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS command_idempotency_keys (
            user_id INTEGER NOT NULL,
            idempotency_key TEXT NOT NULL,
            command TEXT NOT NULL,
            tournament_id INTEGER,
            state TEXT NOT NULL CHECK (state IN ('in_progress', 'completed')),
            response_status INTEGER,
            response_location TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            PRIMARY KEY (user_id, idempotency_key)
        )
        """
    )


def claim_command(conn, user_id, key, command, tournament_id):
    """Claim a key for a new attempt, or return the stored record of a completed one.

    Returns ``None`` when the caller should run the command. Raises an
    ``IdempotencyError`` subclass when the key is invalid, reused elsewhere, or still
    pending.
    """
    if not KEY_PATTERN.fullmatch(key or ""):
        raise InvalidIdempotencyKey(key)

    migrate_idempotency_schema(conn)
    conn.execute(
        "DELETE FROM command_idempotency_keys WHERE state = 'completed' AND completed_at < ?",
        (timestamp_days_ago(RETENTION_DAYS),),
    )
    try:
        conn.execute(
            """
            INSERT INTO command_idempotency_keys
                (user_id, idempotency_key, command, tournament_id, state, created_at)
            VALUES (?, ?, ?, ?, 'in_progress', ?)
            """,
            (user_id, key, command, tournament_id, current_timestamp()),
        )
        conn.commit()
        return None
    except sqlite3.IntegrityError:
        conn.rollback()

    record = conn.execute(
        """
        SELECT command, tournament_id, state, response_status, response_location
        FROM command_idempotency_keys
        WHERE user_id = ? AND idempotency_key = ?
        """,
        (user_id, key),
    ).fetchone()
    if record is None:
        raise IdempotencyInProgress(key)
    if record["command"] != command or record["tournament_id"] != tournament_id:
        raise IdempotencyConflict(key)
    if record["state"] != "completed":
        raise IdempotencyInProgress(key)
    return dict(record)


def complete_command(conn, user_id, key, response_status, response_location):
    conn.execute(
        """
        UPDATE command_idempotency_keys
        SET state = 'completed', response_status = ?, response_location = ?, completed_at = ?
        WHERE user_id = ? AND idempotency_key = ?
        """,
        (response_status, response_location, current_timestamp(), user_id, key),
    )
    conn.commit()
