import sqlite3
import shutil

import pytest
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import generate_password_hash

import config
import app as app_module
import routes.admin as admin_routes
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


@pytest.fixture(scope="session")
def baseline_database(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("database-baseline") / "pytest-baseline.db"
    original_paths = (
        config.DB_PATH,
        common.DB_PATH,
        app_module.DB_PATH,
        admin_routes.DB_PATH,
    )
    db_path_string = str(db_path)
    config.DB_PATH = db_path_string
    common.DB_PATH = db_path_string
    app_module.DB_PATH = db_path_string
    admin_routes.DB_PATH = db_path_string
    try:
        app_module.initialize_app()
        conn = sqlite3.connect(db_path)
        conn.executemany(
            """
            INSERT INTO matches
                (match_date, white_player_id, black_player_id, result, event, notes, round_number)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (f"2026-07-{day:02d}", 1, 2, "1-0" if day % 2 else "0-1", "Pytest baseline", str(day), day)
                for day in range(1, 7)
            ],
        )
        conn.commit()
        conn.close()
        common.refresh_stats()
    finally:
        config.DB_PATH, common.DB_PATH, app_module.DB_PATH, admin_routes.DB_PATH = original_paths
    return db_path


@pytest.fixture(autouse=True)
def isolate_test_database(tmp_path, baseline_database, monkeypatch):
    """Keep every test database operation away from the development database."""
    db_path = tmp_path / "pytest.db"
    shutil.copy2(baseline_database, db_path)
    db_path_string = str(db_path)
    monkeypatch.setattr(config, "DB_PATH", db_path_string)
    monkeypatch.setattr(common, "DB_PATH", db_path_string)
    monkeypatch.setattr(app_module, "DB_PATH", db_path_string)
    monkeypatch.setattr(admin_routes, "DB_PATH", db_path_string)
    yield db_path


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
