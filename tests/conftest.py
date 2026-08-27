import sqlite3

import pytest
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import generate_password_hash

import config
import services.common as common
from app import create_app


def set_admin_session(client, db_path=None):
    """Create a real admin account in the DB and attach a valid admin session."""
    target_db = db_path or config.DB_PATH
    conn = sqlite3.connect(target_db)
    try:
        common.migrate_auth_schema(conn)
        user = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        if user is None:
            conn.execute(
                "INSERT INTO users (username, password_hash, is_active) VALUES (?, ?, 1)",
                ("admin", generate_password_hash("test-admin-password")),
            )
            user_id = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
            role_id = conn.execute("SELECT id FROM roles WHERE name = 'administrator'").fetchone()[0]
            conn.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
                (user_id, role_id),
            )
            conn.commit()
            user = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
    finally:
        conn.close()

    serializer = URLSafeTimedSerializer(client.application.secret_key, salt="wtf-csrf-token")
    raw_token = "test-admin-csrf-token"
    signed_token = serializer.dumps(raw_token)

    with client.session_transaction() as session:
        session.clear()
        session["user_id"] = user[0]
        session["user_role"] = "administrator"
        session["csrf_token"] = raw_token
    return user[0], signed_token


@pytest.fixture
def app():
    """Create and configure a clean testing app instance."""
    app_instance = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SKIP_INIT_DB": True,
        },
        auto_init=False,
    )
    return app_instance


@pytest.fixture
def client(app):
    """A test client for the app fixture."""
    return app.test_client()


@pytest.fixture
def admin_client(client):
    """A test client authenticated as admin."""
    set_admin_session(client)
    return client
