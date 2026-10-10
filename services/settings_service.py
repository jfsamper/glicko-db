"""Persistence and validation for administrator-managed application settings."""
import sqlite3

from config import LOGIN_WINDOW_SECONDS, MAX_LOGIN_ATTEMPTS, PASSWORD_RESET_TTL_SECONDS
from services.display_name import (
    DEFAULT_DISPLAY_NAME_FORMAT,
    DEFAULT_DISPLAY_NAME_TEMPLATE,
    DISPLAY_NAME_FORMATS,
    DISPLAY_NAME_PLACEHOLDERS,
    DISPLAY_NAME_TEMPLATE_MAX_LENGTH,
)


DEFAULT_APPLICATION_SETTINGS = {
    "max_login_attempts": MAX_LOGIN_ATTEMPTS,
    "login_window_seconds": LOGIN_WINDOW_SECONDS,
    "password_reset_ttl_seconds": PASSWORD_RESET_TTL_SECONDS,
    "display_name_format": DEFAULT_DISPLAY_NAME_FORMAT,
    "display_name_template": DEFAULT_DISPLAY_NAME_TEMPLATE,
}

SETTING_LIMITS = {
    "max_login_attempts": (1, 1000),
    "login_window_seconds": (1, 86400),
    "password_reset_ttl_seconds": (300, 604800),
}

# Columns added after the first release. Databases created earlier get them
# through ALTER TABLE; the defaults keep existing players' behavior unchanged.
DISPLAY_NAME_SETTING_COLUMNS = {
    "display_name_format": f"TEXT NOT NULL DEFAULT '{DEFAULT_DISPLAY_NAME_FORMAT}'",
    "display_name_template": f"TEXT NOT NULL DEFAULT '{DEFAULT_DISPLAY_NAME_TEMPLATE}'",
}


def ensure_application_settings_table(conn, defaults=None):
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS application_settings (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            max_login_attempts INTEGER NOT NULL,
            login_window_seconds INTEGER NOT NULL,
            password_reset_ttl_seconds INTEGER NOT NULL,
            display_name_format TEXT NOT NULL DEFAULT '{DEFAULT_DISPLAY_NAME_FORMAT}',
            display_name_template TEXT NOT NULL DEFAULT '{DEFAULT_DISPLAY_NAME_TEMPLATE}',
            updated_at TEXT NOT NULL
        )
        """
    )
    existing_columns = {row[1] for row in conn.execute(
        "PRAGMA table_info(application_settings)").fetchall()}
    for column, definition in DISPLAY_NAME_SETTING_COLUMNS.items():
        if column not in existing_columns:
            conn.execute(
                f"ALTER TABLE application_settings ADD COLUMN {column} {definition}")
    row = conn.execute(
        "SELECT id FROM application_settings WHERE id = 1"
    ).fetchone()
    if row is None:
        from services.timezone_service import current_timestamp

        source = {**DEFAULT_APPLICATION_SETTINGS, **(defaults or {})}
        conn.execute(
            """
            INSERT INTO application_settings
                (id, max_login_attempts, login_window_seconds,
                 password_reset_ttl_seconds, display_name_format,
                 display_name_template, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            """,
            (
                source["max_login_attempts"],
                source["login_window_seconds"],
                source["password_reset_ttl_seconds"],
                source["display_name_format"],
                source["display_name_template"],
                current_timestamp(),
            ),
        )
    conn.commit()


def get_application_settings(conn=None, fallback_settings=None):
    owns_connection = conn is None
    if conn is None:
        from services.db import get_db

        conn = get_db()

    try:
        defaults = dict(DEFAULT_APPLICATION_SETTINGS)
        if fallback_settings:
            defaults.update(fallback_settings)
        ensure_application_settings_table(conn, defaults=defaults)
        row = conn.execute(
            """
            SELECT max_login_attempts, login_window_seconds,
                   password_reset_ttl_seconds, display_name_format,
                   display_name_template, updated_at
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
            "display_name_format": row[3],
            "display_name_template": row[4],
            "updated_at": row[5],
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

    display_format = values.get("display_name_format")
    if display_format not in DISPLAY_NAME_FORMATS:
        raise ValueError("invalid display_name_format")
    template = str(values.get("display_name_template") or "").strip()
    template = template or DEFAULT_DISPLAY_NAME_TEMPLATE
    if len(template) > DISPLAY_NAME_TEMPLATE_MAX_LENGTH:
        raise ValueError("invalid display_name_template")
    if display_format == "custom" and not any(
            placeholder in template for placeholder in DISPLAY_NAME_PLACEHOLDERS):
        raise ValueError("invalid display_name_template")
    validated["display_name_format"] = display_format
    validated["display_name_template"] = template
    return validated


def update_application_settings(values, conn=None):
    """Save application settings. Display names are rendered from them on read."""
    validated = validate_application_settings(values)
    owns_connection = conn is None
    if conn is None:
        from services.db import get_db

        conn = get_db()

    try:
        ensure_application_settings_table(conn)
        from services.timezone_service import current_timestamp

        conn.execute(
            """
            UPDATE application_settings
            SET max_login_attempts = ?,
                login_window_seconds = ?,
                password_reset_ttl_seconds = ?,
                display_name_format = ?,
                display_name_template = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (
                validated["max_login_attempts"],
                validated["login_window_seconds"],
                validated["password_reset_ttl_seconds"],
                validated["display_name_format"],
                validated["display_name_template"],
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
