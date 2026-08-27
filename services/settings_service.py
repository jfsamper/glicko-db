"""Persistence and validation for administrator-managed application settings."""
import sqlite3

from config import LOGIN_WINDOW_SECONDS, MAX_LOGIN_ATTEMPTS, PASSWORD_RESET_TTL_SECONDS


DEFAULT_APPLICATION_SETTINGS = {
    "max_login_attempts": MAX_LOGIN_ATTEMPTS,
    "login_window_seconds": LOGIN_WINDOW_SECONDS,
    "password_reset_ttl_seconds": PASSWORD_RESET_TTL_SECONDS,
}

SETTING_LIMITS = {
    "max_login_attempts": (1, 1000),
    "login_window_seconds": (1, 86400),
    "password_reset_ttl_seconds": (300, 604800),
}


def ensure_application_settings_table(conn, defaults=None):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS application_settings (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            max_login_attempts INTEGER NOT NULL,
            login_window_seconds INTEGER NOT NULL,
            password_reset_ttl_seconds INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    row = conn.execute(
        "SELECT id FROM application_settings WHERE id = 1"
    ).fetchone()
    if row is None:
        from services.common import current_timestamp

        conn.execute(
            """
            INSERT INTO application_settings
                (id, max_login_attempts, login_window_seconds,
                 password_reset_ttl_seconds, updated_at)
            VALUES (1, ?, ?, ?, ?)
            """,
            (
                (defaults or DEFAULT_APPLICATION_SETTINGS)["max_login_attempts"],
                (defaults or DEFAULT_APPLICATION_SETTINGS)["login_window_seconds"],
                (defaults or DEFAULT_APPLICATION_SETTINGS)["password_reset_ttl_seconds"],
                current_timestamp(),
            ),
        )
    conn.commit()


def get_application_settings(conn=None, fallback_settings=None):
    owns_connection = conn is None
    if conn is None:
        from services.common import get_db

        conn = get_db()

    try:
        defaults = dict(DEFAULT_APPLICATION_SETTINGS)
        if fallback_settings:
            defaults.update(fallback_settings)
        ensure_application_settings_table(conn, defaults=defaults)
        row = conn.execute(
            """
            SELECT max_login_attempts, login_window_seconds,
                   password_reset_ttl_seconds, updated_at
            FROM application_settings
            WHERE id = 1
            """
        ).fetchone()
        if row is None:
            return {**defaults, "updated_at": None}
        if isinstance(row, sqlite3.Row):
            return dict(row)
        return {
            "max_login_attempts": row[0],
            "login_window_seconds": row[1],
            "password_reset_ttl_seconds": row[2],
            "updated_at": row[3],
        }
    finally:
        if owns_connection:
            conn.close()


def validate_application_settings(values):
    validated = {}
    for name, (minimum, maximum) in SETTING_LIMITS.items():
        try:
            value = int(values[name])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid {name}") from exc
        if value < minimum or value > maximum:
            raise ValueError(f"invalid {name}")
        validated[name] = value
    return validated


def update_application_settings(values, conn=None):
    validated = validate_application_settings(values)
    owns_connection = conn is None
    if conn is None:
        from services.common import get_db

        conn = get_db()

    try:
        ensure_application_settings_table(conn)
        from services.common import current_timestamp

        conn.execute(
            """
            UPDATE application_settings
            SET max_login_attempts = ?,
                login_window_seconds = ?,
                password_reset_ttl_seconds = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (
                validated["max_login_attempts"],
                validated["login_window_seconds"],
                validated["password_reset_ttl_seconds"],
                current_timestamp(),
            ),
        )
        if owns_connection:
            conn.commit()
        return validated
    finally:
        if owns_connection:
            conn.close()


def migrate_application_settings_schema(conn):
    """Create the application settings row during normal startup or restore."""
    ensure_application_settings_table(conn)
