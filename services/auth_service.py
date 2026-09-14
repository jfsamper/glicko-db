"""Owns user accounts, roles, sessions/permissions, and password-reset flows."""
import hashlib
import secrets
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone as datetime_timezone
from email.message import EmailMessage

from flask import session
from werkzeug.security import check_password_hash, generate_password_hash

from config import MAIL_FROM, MAIL_PASSWORD, MAIL_PORT, MAIL_SERVER, MAIL_USERNAME, MAIL_USE_TLS
from services.db import get_db
from services.timezone_service import current_timestamp

ALLOWED_ROLES = ("administrator", "tournament_director", "operator", "member")


def validate_theme(value):
    if value not in {"light", "dark"}:
        raise ValueError("unsupported theme")
    return value


def validate_email_address(value):
    value = (value or "").strip().lower()
    if not value or "@" not in value or value.startswith("@") or value.endswith("@"):
        raise ValueError("invalid email")
    local_part, domain = value.rsplit("@", 1)
    if not local_part or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("invalid email")
    return value


def migrate_auth_schema(conn):
    """Create the additive auth tables and seed the built-in roles."""
    roles_schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'roles'"
    ).fetchone()
    if roles_schema is not None and "'member'" not in (roles_schema[0] or ""):
        foreign_keys_enabled = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DROP TRIGGER IF EXISTS roles_validate_insert")
        conn.execute("DROP TRIGGER IF EXISTS roles_validate_update")
        conn.execute(
            """
            CREATE TABLE roles_migrated (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE CHECK(name IN ('administrator', 'tournament_director', 'operator', 'member')),
                description TEXT
            )
            """
        )
        conn.execute("INSERT INTO roles_migrated (id, name, description) SELECT id, name, description FROM roles")
        conn.execute("DROP TABLE roles")
        conn.execute("ALTER TABLE roles_migrated RENAME TO roles")
        conn.commit()
        conn.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys_enabled else 'OFF'}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE CHECK(name IN ('administrator', 'tournament_director', 'operator', 'member')),
            description TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS roles_validate_insert
        BEFORE INSERT ON roles
        BEGIN
            SELECT CASE
                WHEN NEW.name NOT IN ('administrator', 'tournament_director', 'operator', 'member')
                THEN RAISE(ABORT, 'role name not allowed')
            END;
        END;
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS roles_validate_update
        BEFORE UPDATE ON roles
        BEGIN
            SELECT CASE
                WHEN NEW.name NOT IN ('administrator', 'tournament_director', 'operator', 'member')
                THEN RAISE(ABORT, 'role name not allowed')
            END;
        END;
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_login_at TEXT,
            timezone TEXT,
            email TEXT,
            player_id INTEGER,
            language TEXT DEFAULT 'es',
            theme TEXT DEFAULT 'light'
        )
        """
    )
    user_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
    }
    for column_name in ("timezone", "email", "player_id", "language", "theme"):
        if column_name not in user_columns:
            definition = "INTEGER" if column_name == "player_id" else "TEXT"
            conn.execute(f"ALTER TABLE users ADD COLUMN {column_name} {definition}")
    conn.execute("UPDATE users SET language = 'es' WHERE language IS NULL")
    conn.execute("UPDATE users SET theme = 'light' WHERE theme IS NULL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            UNIQUE(user_id, role_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(role_id) REFERENCES roles(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user ON password_reset_tokens (user_id, created_at DESC)"
    )

    for role_name in ALLOWED_ROLES:
        conn.execute(
            "INSERT OR IGNORE INTO roles (name, description) VALUES (?, ?)",
            (role_name, role_name),
        )

    conn.commit()


def migrate_result_submissions_schema(conn):
    """Create the moderation queue without exposing submissions as live matches."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS result_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submitted_by_user_id INTEGER NOT NULL,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL CHECK(result IN ('1-0', '0-1', '1/2-1/2')),
            event TEXT,
            location TEXT,
            notes TEXT,
            round_number INTEGER NOT NULL DEFAULT 0,
            handicap_stones INTEGER NOT NULL DEFAULT 0,
            sgf_filename TEXT,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
            reviewed_by_user_id INTEGER,
            reviewed_at TEXT,
            review_notes TEXT,
            approval_code_hash TEXT UNIQUE,
            approval_code_expires_at TEXT,
            approval_code_used_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(submitted_by_user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(white_player_id) REFERENCES players(id) ON DELETE RESTRICT,
            FOREIGN KEY(black_player_id) REFERENCES players(id) ON DELETE RESTRICT,
            FOREIGN KEY(reviewed_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
            CHECK(white_player_id != black_player_id)
        )
        """
    )
    submission_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(result_submissions)").fetchall()
    }
    if submission_columns and "sgf_filename" not in submission_columns:
        conn.execute("ALTER TABLE result_submissions ADD COLUMN sgf_filename TEXT")
    if submission_columns and "location" not in submission_columns:
        conn.execute("ALTER TABLE result_submissions ADD COLUMN location TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_result_submissions_status ON result_submissions (status, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_result_submissions_submitter ON result_submissions (submitted_by_user_id, created_at DESC)"
    )
    conn.commit()


def create_result_approval_code(submission_id, conn=None, ttl_hours=48):
    """Create a hashed, expiring code for the future email approval flow."""
    owns_connection = conn is None
    conn = conn or get_db()
    try:
        raw_code = f"{secrets.randbelow(100000000):08d}"
        code_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
        expires_at = datetime.now(datetime_timezone.utc) + timedelta(hours=ttl_hours)
        conn.execute(
            """
            UPDATE result_submissions
            SET approval_code_hash = ?, approval_code_expires_at = ?, approval_code_used_at = NULL
            WHERE id = ? AND status = 'pending'
            """,
            (code_hash, _utc_timestamp(expires_at), submission_id),
        )
        if owns_connection:
            conn.commit()
        return raw_code
    finally:
        if owns_connection:
            conn.close()


def consume_result_approval_code(raw_code, conn=None):
    """Consume a valid email approval code; the caller still performs review policy."""
    owns_connection = conn is None
    conn = conn or get_db()
    try:
        code_hash = hashlib.sha256((raw_code or "").encode("utf-8")).hexdigest()
        now = _utc_timestamp(datetime.now(datetime_timezone.utc))
        row = conn.execute(
            """
            SELECT id FROM result_submissions
            WHERE approval_code_hash = ?
              AND status = 'pending'
              AND approval_code_used_at IS NULL
              AND approval_code_expires_at > ?
            """,
            (code_hash, now),
        ).fetchone()
        if row is None:
            return None
        submission_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        conn.execute(
            "UPDATE result_submissions SET approval_code_used_at = ? WHERE id = ?",
            (now, submission_id),
        )
        if owns_connection:
            conn.commit()
        return submission_id
    finally:
        if owns_connection:
            conn.close()


def bootstrap_default_admin_account(conn=None, password=None):
    """Create the one-time default admin account when configured via env/password."""
    import os

    conn = conn or get_db()
    password = password or os.environ["ADMIN_PASSWORD"]
    if not password:
        return None

    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count > 0:
        return None

    username = "admin"
    existing = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if existing is not None:
        return existing[0]

    user_id = conn.execute(
        "INSERT INTO users (username, password_hash, is_active, created_at) VALUES (?, ?, 1, ?)",
        (username, generate_password_hash(password), current_timestamp()),
    ).lastrowid

    role_id = conn.execute(
        "SELECT id FROM roles WHERE name = 'administrator'"
    ).fetchone()
    if role_id is not None:
        conn.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
            (user_id, role_id[0]),
        )

    conn.commit()
    return user_id


def create_user_account(
    username,
    password,
    role_name="operator",
    timezone_name=None,
    conn=None,
    email=None,
    player_id=None,
):
    """Create a named user account with a hashed password and role assignment."""
    from services.timezone_service import validate_timezone

    if not isinstance(username, str) or not username.strip():
        raise ValueError("username is required")
    if not isinstance(password, str) or not password:
        raise ValueError("password is required")

    normalized_username = username.strip()
    role_name = (role_name or "operator").strip()
    if role_name not in ALLOWED_ROLES:
        raise ValueError("unsupported role")
    timezone_name = validate_timezone(timezone_name)
    email = validate_email_address(email) if email else None

    owns_connection = conn is None
    conn = conn or get_db()
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row

    try:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (normalized_username,)).fetchone():
            raise ValueError("username already exists")
        if email and conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise ValueError("email already exists")
        if player_id is not None:
            player = conn.execute("SELECT id FROM players WHERE id = ?", (player_id,)).fetchone()
            if player is None:
                raise ValueError("player not found")

        role = conn.execute("SELECT id FROM roles WHERE name = ?", (role_name,)).fetchone()
        if role is None:
            raise ValueError("role not found")

        user_id = conn.execute(
            "INSERT INTO users (username, password_hash, is_active, created_at, timezone, email, player_id, language, theme) VALUES (?, ?, 1, ?, ?, ?, ?, 'es', 'light')",
            (normalized_username, generate_password_hash(password), current_timestamp(), timezone_name, email, player_id),
        ).lastrowid
        conn.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
            (user_id, role["id"]),
        )
        conn.commit()
        return user_id
    finally:
        if owns_connection:
            conn.close()


def get_current_user(conn=None):
    user_id = session.get("user_id")
    if user_id is None:
        return None

    owns_connection = conn is None
    conn = conn or get_db()
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row

    try:
        users_table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if users_table_exists is None:
            session.clear()
            return None

        user = conn.execute(
            """
                 SELECT u.id, u.username, u.password_hash, u.is_active,
                     u.email, u.language, u.theme, u.timezone, u.player_id
            FROM users u
            WHERE u.id = ?
            """,
            (user_id,),
        ).fetchone()
        if user is None or user["is_active"] != 1:
            session.clear()
            return None

        role = conn.execute(
            """
            SELECT r.name
            FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE ur.user_id = ?
            ORDER BY r.name
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        payload = dict(user)
        payload["role"] = role["name"] if role else None
        return payload
    finally:
        if owns_connection:
            conn.close()


def user_has_permission(permission_name):
    user = get_current_user()
    if user is None:
        return False

    role = user.get("role")
    if permission_name == "admin":
        return role == "administrator"
    if permission_name == "tournament_admin":
        return role in {"administrator", "tournament_director"}
    if permission_name == "data_admin":
        return role in {"administrator", "operator"}
    if permission_name == "operator":
        return role in {"administrator", "tournament_director", "operator"}
    if permission_name == "dashboard":
        return role in {"administrator", "tournament_director", "operator"}
    if permission_name == "results_submitter":
        return role in {"administrator", "tournament_director", "operator", "member"}
    return role == "administrator"


def authenticate_user(username, password, conn=None):
    owns_connection = conn is None
    conn = conn or get_db()
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row

    try:
        users_table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if users_table_exists is None:
            return None

        user = conn.execute(
            "SELECT id, username, password_hash, is_active, email, language, theme, timezone, player_id FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if user is None or user["is_active"] != 1:
            return None
        if check_password_hash(user["password_hash"], password):
            role = conn.execute(
                """
                SELECT r.name
                FROM user_roles ur
                JOIN roles r ON r.id = ur.role_id
                WHERE ur.user_id = ?
                ORDER BY r.name
                LIMIT 1
                """,
                (user["id"],),
            ).fetchone()
            payload = dict(user)
            payload["role"] = role["name"] if role else None
            return payload
        return None
    finally:
        if owns_connection:
            conn.close()


def _utc_timestamp(value):
    return value.astimezone(datetime_timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def create_password_reset_token(user_id, conn=None):
    """Create a single-use password reset token and return its raw value."""
    from services.settings_service import get_application_settings

    owns_connection = conn is None
    conn = conn or get_db()
    try:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        now = datetime.now(datetime_timezone.utc)
        reset_ttl = get_application_settings(conn=conn)["password_reset_ttl_seconds"]
        expires_at = now + timedelta(seconds=reset_ttl)
        conn.execute(
            "DELETE FROM password_reset_tokens WHERE user_id = ? AND used_at IS NULL",
            (user_id,),
        )
        conn.execute(
            """
            INSERT INTO password_reset_tokens
                (user_id, token_hash, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, token_hash, _utc_timestamp(expires_at), _utc_timestamp(now)),
        )
        if owns_connection:
            conn.commit()
        return raw_token
    finally:
        if owns_connection:
            conn.close()


def reset_password_with_token(raw_token, new_password, conn=None):
    """Consume a valid reset token and replace the account password."""
    if not isinstance(new_password, str) or len(new_password) < 8:
        raise ValueError("password too short")
    owns_connection = conn is None
    conn = conn or get_db()
    try:
        token_hash = hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()
        now = _utc_timestamp(datetime.now(datetime_timezone.utc))
        row = conn.execute(
            """
            SELECT prt.id
            FROM password_reset_tokens prt
            JOIN users u ON u.id = prt.user_id
            WHERE prt.token_hash = ?
              AND prt.used_at IS NULL
              AND prt.expires_at > ?
              AND u.is_active = 1
            """,
            (token_hash, now),
        ).fetchone()
        if row is None:
            return False
        token_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = (SELECT user_id FROM password_reset_tokens WHERE id = ?)",
            (generate_password_hash(new_password), token_id),
        )
        conn.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE id = ?",
            (now, token_id),
        )
        if owns_connection:
            conn.commit()
        return True
    finally:
        if owns_connection:
            conn.close()


def send_password_reset_email(recipient, reset_url):
    """Send a password reset email using the configured SMTP server."""
    from services.settings_service import get_application_settings

    if not MAIL_SERVER:
        raise RuntimeError("password reset email is not configured")
    reset_ttl = get_application_settings()["password_reset_ttl_seconds"]
    message = EmailMessage()
    message["Subject"] = "Password reset"
    message["From"] = MAIL_FROM or MAIL_USERNAME or "no-reply@localhost"
    message["To"] = recipient
    message.set_content(
        "Use this link to set a new password for your account:\n\n"
        f"{reset_url}\n\n"
        f"This link expires in {max(1, reset_ttl // 60)} minutes."
    )
    with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=10) as smtp:
        if MAIL_USE_TLS:
            smtp.starttls()
        if MAIL_USERNAME:
            smtp.login(MAIL_USERNAME, MAIL_PASSWORD)
        smtp.send_message(message)


def admin_required(permission_name="operator"):
    user = get_current_user()
    if user is None:
        return False

    return user_has_permission(permission_name)
