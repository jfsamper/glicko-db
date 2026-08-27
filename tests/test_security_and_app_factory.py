import json
import re
import sqlite3
from pathlib import Path

import conftest
import pytest
from flask import url_for
from flask_wtf.csrf import generate_csrf
from werkzeug.security import generate_password_hash

from app import create_app
import config
import services.common as common
import routes.admin as admin_routes


def test_create_app_factory_creates_isolated_instances(tmp_path):
    app1 = create_app({"TESTING": True, "CUSTOM_TEST_KEY": "val1", "SKIP_INIT_DB": True}, auto_init=False)
    app2 = create_app({"TESTING": True, "CUSTOM_TEST_KEY": "val2", "SKIP_INIT_DB": True}, auto_init=False)

    assert app1.config["CUSTOM_TEST_KEY"] == "val1"
    assert app2.config["CUSTOM_TEST_KEY"] == "val2"
    assert app1 is not app2


def test_session_cookie_security_defaults():
    test_app = create_app({"TESTING": True, "SKIP_INIT_DB": True}, auto_init=False)
    assert test_app.config["SESSION_COOKIE_SECURE"] is True
    assert test_app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert test_app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_csrf_protection_rejects_missing_token_when_active(monkeypatch, tmp_path):
    db_path = tmp_path / "csrf_test.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    test_app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": True,
            "WTF_CSRF_ENABLED_IN_TESTS": True,
            "WTF_CSRF_CHECK_DEFAULT": True,
            "SECRET_KEY": "test-secret-key",
            "SKIP_INIT_DB": True,
        },
        auto_init=False,
    )

    client = test_app.test_client()
    conftest.set_admin_session(client, db_path)

    # POST without CSRF token must fail with 400
    response = client.post("/admin/tournaments", data={"action": "create", "name": "No CSRF"})
    assert response.status_code == 400


def test_csrf_protection_accepts_valid_token(monkeypatch, tmp_path):
    db_path = tmp_path / "csrf_valid_test.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT,
            begin_date TEXT,
            end_date TEXT,
            rounds INTEGER NOT NULL DEFAULT 1,
            tournament_type TEXT NOT NULL DEFAULT 'swiss',
            pairing_system TEXT NOT NULL DEFAULT 'swiss',
            bye_points REAL NOT NULL DEFAULT 1,
            absent_points REAL NOT NULL DEFAULT 0,
            placement_criteria TEXT NOT NULL DEFAULT 'NBW,SOS,SOSOS',
            mm_bar INTEGER NOT NULL DEFAULT 8,
            mm_floor INTEGER NOT NULL DEFAULT -30,
            mm_zero INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'draft',
            source_format TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()

    test_app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": True,
            "WTF_CSRF_ENABLED_IN_TESTS": True,
            "WTF_CSRF_CHECK_DEFAULT": True,
            "SECRET_KEY": "test-secret-key",
            "SKIP_INIT_DB": True,
        },
        auto_init=False,
    )

    client = test_app.test_client()
    client.get("/admin/login")
    _, token = conftest.set_admin_session(client, db_path)

    response = client.post(
        "/admin/tournaments",
        data={
            "csrf_token": token,
            "action": "create",
            "name": "CSRF Tournament",
            "rounds": "3",
            "pairing_system": "swiss",
            "bye_points": "1",
            "absent_points": "0",
        },
    )
    assert response.status_code == 302


def test_admin_blueprint_before_request_guards_protected_views(tmp_path):
    test_app = create_app({"TESTING": True, "SKIP_INIT_DB": True}, auto_init=False)
    client = test_app.test_client()

    # Unauthenticated user visiting /admin gets redirected to login
    response = client.get("/admin?lang=en")
    assert response.status_code == 302
    assert "/admin/login" in response.headers["Location"]

    # Login page itself is accessible without admin session
    response = client.get("/admin/login?lang=en")
    assert response.status_code == 200


def test_url_for_supports_both_unprefixed_and_blueprint_names():
    test_app = create_app({"TESTING": True, "SKIP_INIT_DB": True}, auto_init=False)

    with test_app.test_request_context():
        # Public routes
        assert url_for("index") == "/"
        assert url_for("public.index") == "/"
        assert url_for("rankings") == "/rankings"
        assert url_for("public.rankings") == "/rankings"
        assert url_for("players") == "/players"
        assert url_for("public.players") == "/players"

        # Admin routes
        assert url_for("admin") == "/admin"
        assert url_for("admin.admin") == "/admin"
        assert url_for("admin_login") == "/admin/login"
        assert url_for("admin.admin_login") == "/admin/login"


def test_role_permissions_allow_tournament_director_but_not_admin_only_access(tmp_path, monkeypatch):
    db_path = tmp_path / "auth_roles.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    app_instance = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "SECRET_KEY": "test-role-secret", "SKIP_INIT_DB": True}, auto_init=False)

    with app_instance.app_context():
        conn = sqlite3.connect(db_path)
        common.migrate_auth_schema(conn)
        conn.execute(
            "INSERT INTO users (username, password_hash, is_active) VALUES (?, ?, 1)",
            ("director", "sha256"),
        )
        user_id = conn.execute("SELECT id FROM users WHERE username = 'director'").fetchone()[0]
        role_id = conn.execute("SELECT id FROM roles WHERE name = 'tournament_director'").fetchone()[0]
        conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))
        conn.commit()
        conn.close()

    with app_instance.test_request_context():
        from flask import session

        session["user_id"] = user_id
        session["user_role"] = "tournament_director"
        assert common.admin_required() is True
        assert common.user_has_permission("tournament_admin") is True
        assert common.user_has_permission("data_admin") is False
        assert common.user_has_permission("admin") is False

    assert admin_routes.get_required_permission_for_route("admin.admin_players") == "data_admin"
    assert admin_routes.get_required_permission_for_route("admin.admin_edit_player") == "data_admin"
    assert admin_routes.get_required_permission_for_route("admin.admin_delete_player") == "data_admin"
    assert admin_routes.get_required_permission_for_route("admin.admin_ratings") == "data_admin"
    assert admin_routes.get_required_permission_for_route("admin.admin_categories") == "data_admin"


def test_tournament_player_management_can_create_and_enroll_player(tmp_path, monkeypatch):
    db_path = tmp_path / "tournament_create_player.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    app_instance = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "SECRET_KEY": "test-create-player", "SKIP_INIT_DB": True}, auto_init=False)
    with app_instance.app_context():
        conn = sqlite3.connect(db_path)
        common.migrate_auth_schema(conn)
        conn.executescript(
            """
            CREATE TABLE tournaments (id INTEGER PRIMARY KEY, name TEXT, pairing_system TEXT, tournament_type TEXT);
            CREATE TABLE players (
                id INTEGER PRIMARY KEY AUTOINCREMENT, first_name TEXT NOT NULL, last_name TEXT NOT NULL,
                display_name TEXT NOT NULL, country TEXT, club TEXT, slug TEXT UNIQUE, active INTEGER DEFAULT 1,
                rating REAL DEFAULT 1500, initial_rating REAL, rd REAL DEFAULT 350, volatility REAL DEFAULT 0.06
            );
            CREATE TABLE tournament_participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT, tournament_id INTEGER, player_id INTEGER,
                seed_rating REAL DEFAULT 0, seed_rank INTEGER DEFAULT 0, category TEXT DEFAULT '',
                initial_score REAL DEFAULT 0, acceleration REAL DEFAULT 0, UNIQUE(tournament_id, player_id)
            );
            CREATE TABLE tournament_pending_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT, tournament_id INTEGER, display_name TEXT,
                suggested_name TEXT, resolved_player_id INTEGER, rating REAL DEFAULT 0,
                rank INTEGER DEFAULT 0, category TEXT DEFAULT '', source_key TEXT
            );
            INSERT INTO tournaments VALUES (1, 'Test tournament', 'swiss', 'swiss');
            """
        )
        conn.execute("INSERT INTO users (username, password_hash, is_active) VALUES (?, ?, 1)", ("operator", "sha256"))
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (?, (SELECT id FROM roles WHERE name = 'operator'))",
            (user_id,),
        )
        conn.commit()
        conn.close()

    client = app_instance.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["user_role"] = "operator"

    response = client.get("/admin/tournaments/1/players?lang=en")
    assert response.status_code == 200
    assert b"Create pending player" in response.data
    option_match = re.search(
        rb'<option value="([0-9]+)">4 dan</option>',
        response.data,
    )
    assert option_match is not None
    selected_glicko = option_match.group(1).decode()

    response = client.post(
        "/admin/tournaments/1/players/create?lang=en",
        data={"first_name": "Ada", "last_name": "Lovelace", "glicko": selected_glicko},
    )
    assert response.status_code == 302

    conn = sqlite3.connect(db_path)
    player = conn.execute("SELECT id FROM players").fetchone()
    participant = conn.execute(
        "SELECT 1 FROM tournament_participants WHERE tournament_id = 1 AND player_id = ?",
        (player[0] if player else None,),
    ).fetchone()
    pending = conn.execute(
        "SELECT id, display_name, rating, rank FROM tournament_pending_players WHERE tournament_id = 1"
    ).fetchone()
    conn.close()
    assert player is None
    assert participant is None
    assert tuple(pending[1:]) == ("Ada Lovelace", float(selected_glicko), 1)

    management_page = client.get("/admin/tournaments/1/players?lang=en")
    assert management_page.status_code == 200
    assert b"/admin/tournaments/1/pending-player/delete?lang=en" in management_page.data
    assert b'name="pending_id" value="' in management_page.data

    response = client.post(
        "/admin/tournaments/1/pending-player/delete?lang=en",
        data={"pending_id": pending[0], "return_to": "players"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/tournaments/1/players?lang=en")

    conn = sqlite3.connect(db_path)
    remaining_pending = conn.execute(
        "SELECT COUNT(*) FROM tournament_pending_players WHERE tournament_id = 1"
    ).fetchone()[0]
    conn.close()
    assert remaining_pending == 0

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO players (first_name, last_name, display_name, rating) VALUES (?, ?, ?, ?)",
        ("Ada", "Lovelace", "Ada Lovelace", 1800),
    )
    conn.commit()
    conn.close()

    response = client.post(
        "/admin/tournaments/1/players/create?lang=en",
        data={"first_name": "Ada", "last_name": "Lovelace", "glicko": selected_glicko},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"already exists" in response.data

    response = client.post(
        "/admin/tournaments/1/players/create?lang=en",
        data={"first_name": "Ada", "last_name": "Lovelacee", "glicko": selected_glicko},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"similar name" in response.data

    conn = sqlite3.connect(db_path)
    remaining_pending = conn.execute(
        "SELECT COUNT(*) FROM tournament_pending_players WHERE tournament_id = 1"
    ).fetchone()[0]
    conn.close()
    assert remaining_pending == 0


def test_tournament_player_creation_rejects_missing_name(tmp_path, monkeypatch):
    db_path = tmp_path / "tournament_create_player_validation.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    app_instance = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "SECRET_KEY": "test-create-validation", "SKIP_INIT_DB": True}, auto_init=False)
    with app_instance.app_context():
        conn = sqlite3.connect(db_path)
        common.migrate_auth_schema(conn)
        conn.executescript(
            """
            CREATE TABLE tournaments (id INTEGER PRIMARY KEY, name TEXT, pairing_system TEXT, tournament_type TEXT);
            CREATE TABLE players (id INTEGER PRIMARY KEY AUTOINCREMENT, first_name TEXT NOT NULL, last_name TEXT NOT NULL, display_name TEXT NOT NULL, slug TEXT UNIQUE, active INTEGER DEFAULT 1, rating REAL DEFAULT 1500, rd REAL DEFAULT 350, volatility REAL DEFAULT 0.06);
            CREATE TABLE tournament_participants (id INTEGER PRIMARY KEY AUTOINCREMENT, tournament_id INTEGER, player_id INTEGER, seed_rating REAL DEFAULT 0, seed_rank INTEGER DEFAULT 0, category TEXT DEFAULT '', initial_score REAL DEFAULT 0, acceleration REAL DEFAULT 0, UNIQUE(tournament_id, player_id));
            INSERT INTO tournaments VALUES (1, 'Test tournament', 'swiss', 'swiss');
            """
        )
        conn.execute("INSERT INTO users (username, password_hash, is_active) VALUES (?, ?, 1)", ("operator", "sha256"))
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, (SELECT id FROM roles WHERE name = 'operator'))", (user_id,))
        conn.commit()
        conn.close()

    client = app_instance.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["user_role"] = "operator"
    response = client.post("/admin/tournaments/1/players/create?lang=en", data={"first_name": "Ada", "last_name": "Lovelace", "glicko": "0"})

    assert response.status_code == 302
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 0
    conn.close()


def test_get_current_user_keeps_caller_owned_connections_open(tmp_path, monkeypatch):
    db_path = tmp_path / "auth_connection.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    app_instance = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "SECRET_KEY": "test-conn-secret", "SKIP_INIT_DB": True}, auto_init=False)

    with app_instance.app_context():
        conn = sqlite3.connect(db_path)
        common.migrate_auth_schema(conn)
        conn.execute("INSERT INTO users (username, password_hash, is_active) VALUES (?, ?, 1)", ("ops", "sha256"))
        user_id = conn.execute("SELECT id FROM users WHERE username = 'ops'").fetchone()[0]
        conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, (SELECT id FROM roles WHERE name = 'operator'))", (user_id,))
        conn.commit()

        with app_instance.test_request_context():
            from flask import session

            session["user_id"] = user_id
            session["user_role"] = "operator"

            owned_conn = sqlite3.connect(str(db_path))
            assert common.get_current_user(conn=owned_conn) is not None
            assert owned_conn.execute("SELECT 1").fetchone()[0] == 1
            owned_conn.close()

        conn.close()


def test_non_admin_role_session_remains_valid_across_requests(tmp_path, monkeypatch):
    db_path = tmp_path / "auth_session_guard.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    app_instance = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-session-secret",
            "SKIP_INIT_DB": True,
        },
        auto_init=False,
    )

    with app_instance.app_context():
        conn = sqlite3.connect(db_path)
        common.migrate_auth_schema(conn)
        conn.execute(
            "INSERT INTO users (username, password_hash, is_active) VALUES (?, ?, 1)",
            ("director", "sha256"),
        )
        user_id = conn.execute("SELECT id FROM users WHERE username = 'director'").fetchone()[0]
        role_id = conn.execute("SELECT id FROM roles WHERE name = 'tournament_director'").fetchone()[0]
        conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))
        conn.commit()
        conn.close()

    client = app_instance.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["user_role"] = "tournament_director"

    response = client.get("/admin?lang=en")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Import" in page
    assert "Tournament operations" in page
    assert "Data management" not in page
    assert 'href="/admin/players' not in page
    assert 'href="/admin/ratings' not in page
    assert 'href="/admin/categories' not in page
    assert "User Management" not in page
    assert "Audit review" not in page
    assert "Backups" not in page


def test_admin_login_authenticates_named_accounts_not_only_bootstrap_admin(tmp_path, monkeypatch):
    db_path = tmp_path / "auth_named_login.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    app_instance = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-named-login-secret",
            "SKIP_INIT_DB": True,
        },
        auto_init=False,
    )

    with app_instance.app_context():
        conn = sqlite3.connect(db_path)
        common.migrate_auth_schema(conn)
        role_id = conn.execute("SELECT id FROM roles WHERE name = 'tournament_director'").fetchone()[0]
        conn.execute(
            "INSERT INTO users (username, password_hash, is_active) VALUES (?, ?, 1)",
            ("director", generate_password_hash("director-pass")),
        )
        user_id = conn.execute("SELECT id FROM users WHERE username = 'director'").fetchone()[0]
        conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))
        conn.commit()
        conn.close()

    client = app_instance.test_client()
    response = client.post(
        "/admin/login?lang=en",
        data={"username": "director", "password": "director-pass"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["user_id"] == user_id
        assert session["user_role"] == "tournament_director"


def test_admin_backups_requires_administrator_capability(tmp_path, monkeypatch):
    db_path = tmp_path / "auth_capability.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    app_instance = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-capability-secret",
            "SKIP_INIT_DB": True,
        },
        auto_init=False,
    )

    with app_instance.app_context():
        conn = sqlite3.connect(db_path)
        common.migrate_auth_schema(conn)
        role_id = conn.execute("SELECT id FROM roles WHERE name = 'operator'").fetchone()[0]
        conn.execute(
            "INSERT INTO users (username, password_hash, is_active) VALUES (?, ?, 1)",
            ("operator", generate_password_hash("operator-pass")),
        )
        user_id = conn.execute("SELECT id FROM users WHERE username = 'operator'").fetchone()[0]
        conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))
        conn.commit()
        conn.close()

    client = app_instance.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["user_role"] = "operator"

    response = client.get("/admin/backups?lang=en")
    assert response.status_code == 403


def test_restore_db_from_backup_recreates_auth_schema_for_session_checks(tmp_path, monkeypatch):
    db_path = tmp_path / "restored_auth_backup.db"
    legacy_backup_path = tmp_path / "legacy_backup_without_auth.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    app_instance = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-restore-secret",
            "SKIP_INIT_DB": True,
        },
        auto_init=False,
    )

    with sqlite3.connect(legacy_backup_path) as conn:
        conn.execute(
            "CREATE TABLE players (id INTEGER PRIMARY KEY AUTOINCREMENT, display_name TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO players (display_name) VALUES (?)",
            ("Legacy backup player",),
        )
        conn.commit()

    import routes.admin as admin_routes
    monkeypatch.setattr(admin_routes, "DB_PATH", str(db_path), raising=False)

    assert admin_routes.restore_db_from_backup(str(legacy_backup_path)) is True

    client = app_instance.test_client()
    conftest.set_admin_session(client, db_path)

    response = client.get("/admin/backups?lang=en")
    assert response.status_code == 200


def test_migrate_auth_schema_rejects_unknown_roles(tmp_path, monkeypatch):
    db_path = tmp_path / "auth_role_whitelist.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    conn = sqlite3.connect(db_path)
    common.migrate_auth_schema(conn)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO roles (name, description) VALUES (?, ?)",
            ("guest", "guest role"),
        )
        conn.commit()

    conn.close()


def test_auth_schema_bootstraps_initial_administrator_account(tmp_path, monkeypatch):
    db_path = tmp_path / "auth_bootstrap.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    conn = sqlite3.connect(db_path)
    common.migrate_auth_schema(conn)
    common.bootstrap_default_admin_account(conn, password="bootstrap-secret")

    created = conn.execute(
        "SELECT username, is_active FROM users WHERE username = 'admin'"
    ).fetchone()
    assert created is not None
    assert created[1] == 1

    role_name = conn.execute(
        """
        SELECT r.name
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        WHERE ur.user_id = ?
        """,
        (conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0],),
    ).fetchone()
    assert role_name is not None
    assert role_name[0] == "administrator"
    conn.close()


def test_admin_login_requires_named_account_not_shared_password_fallback(tmp_path, monkeypatch):
    db_path = tmp_path / "auth_login_requires_named_user.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    app_instance = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-auth-secret",
            "SKIP_INIT_DB": True,
        },
        auto_init=False,
    )

    with app_instance.app_context():
        conn = sqlite3.connect(db_path)
        common.migrate_auth_schema(conn)
        conn.close()

    client = app_instance.test_client()
    response = client.post(
        "/admin/login?lang=en",
        data={"username": "admin", "password": "login-secret"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    with client.session_transaction() as session:
        assert session.get("user_id") is None


def test_admin_user_management_page_and_create_form_render_for_administrators(tmp_path, monkeypatch):
    db_path = tmp_path / "user_management_render.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    app_instance = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-user-management-secret",
            "SKIP_INIT_DB": True,
        },
        auto_init=False,
    )

    with app_instance.app_context():
        conn = sqlite3.connect(db_path)
        common.migrate_auth_schema(conn)
        role_id = conn.execute("SELECT id FROM roles WHERE name = 'administrator'").fetchone()[0]
        conn.execute(
            "INSERT INTO users (username, password_hash, is_active) VALUES (?, ?, 1)",
            ("manager", generate_password_hash("manager-pass")),
        )
        user_id = conn.execute("SELECT id FROM users WHERE username = 'manager'").fetchone()[0]
        conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))
        conn.commit()
        conn.close()

    client = app_instance.test_client()
    conftest.set_admin_session(client, db_path)

    response = client.get("/admin/users?lang=en")
    assert response.status_code == 200
    assert b"User Management" in response.data
    assert b"manager" in response.data

    create_response = client.get("/admin/users/create?lang=en")
    assert create_response.status_code == 200
    assert b"Create user" in create_response.data


def test_admin_user_timezone_can_be_created_and_updated(tmp_path, monkeypatch):
    db_path = tmp_path / "user_timezone.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    app_instance = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-user-timezone-secret",
            "SKIP_INIT_DB": True,
        },
        auto_init=False,
    )

    with app_instance.app_context():
        conn = sqlite3.connect(db_path)
        common.migrate_auth_schema(conn)
        admin_role_id = conn.execute(
            "SELECT id FROM roles WHERE name = 'administrator'"
        ).fetchone()[0]
        manager_id = conn.execute(
            "INSERT INTO users (username, password_hash, is_active) VALUES (?, ?, 1)",
            ("manager", generate_password_hash("manager-pass")),
        ).lastrowid
        conn.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
            (manager_id, admin_role_id),
        )
        conn.commit()
        conn.close()

    client = app_instance.test_client()
    with client.session_transaction() as session:
        session["user_id"] = manager_id
        session["user_role"] = "administrator"

    response = client.post(
        "/admin/users/create?lang=en",
        data={
            "username": "tokyo-user",
            "password": "tokyo-pass",
            "role_name": "operator",
            "timezone": "Asia/Tokyo",
        },
    )
    assert response.status_code == 302

    with sqlite3.connect(db_path) as conn:
        created_id = conn.execute(
            "SELECT id FROM users WHERE username = 'tokyo-user'"
        ).fetchone()[0]
        assert conn.execute(
            "SELECT timezone FROM users WHERE id = ?", (created_id,)
        ).fetchone()[0] == "Asia/Tokyo"

    response = client.post(
        f"/admin/users/{created_id}/edit?lang=en",
        data={
            "username": "tokyo-user",
            "role_name": "operator",
            "timezone": "Europe/Madrid",
            "is_active": "1",
        },
    )
    assert response.status_code == 302

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT timezone FROM users WHERE id = ?", (created_id,)
        ).fetchone()[0] == "Europe/Madrid"

def test_admin_audit_log_tracks_login_logout_and_config_changes(tmp_path, monkeypatch):
    db_path = tmp_path / "audit_log.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    app_instance = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-audit-secret",
            "SKIP_INIT_DB": True,
        },
        auto_init=False,
    )

    with app_instance.app_context():
        conn = sqlite3.connect(db_path)
        common.migrate_auth_schema(conn)
        common.migrate_audit_log_schema(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT,
                rating REAL DEFAULT 1500,
                games_played INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                country TEXT,
                club TEXT,
                slug TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS category_config (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                glicko_k REAL,
                glicko_m REAL,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO category_config (id, glicko_k, glicko_m, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (1, 20.0, 400.0),
        )
        conn.execute(
            "INSERT INTO players (display_name, rating, games_played, active) VALUES (?, ?, ?, ?)",
            ("Audit Player", 1700.0, 3, 1),
        )
        role_id = conn.execute("SELECT id FROM roles WHERE name = 'administrator' ").fetchone()[0]
        conn.execute(
            "INSERT INTO users (username, password_hash, is_active) VALUES (?, ?, 1)",
            ("audit-admin", generate_password_hash("strong-pass")),
        )
        user_id = conn.execute("SELECT id FROM users WHERE username = 'audit-admin' ").fetchone()[0]
        conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))
        conn.commit()
        conn.close()

    client = app_instance.test_client()
    login_response = client.post(
        "/admin/login?lang=en",
        data={"username": "audit-admin", "password": "strong-pass"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302

    with client.session_transaction() as session:
        current_user_id = session["user_id"]

    client.post(
        "/admin/categories?lang=en",
        data={"action": "save", "glicko_k": "20", "glicko_m": "400"},
        follow_redirects=False,
    )

    logout_response = client.get("/admin/logout?lang=en", follow_redirects=False)
    assert logout_response.status_code == 302

    with sqlite3.connect(db_path) as conn:
        actions = conn.execute(
            "SELECT action_type, user_id, resource_type FROM audit_log ORDER BY id"
        ).fetchall()

    recorded = {row[0]: row[1:] for row in actions}
    assert recorded["login"][0] == current_user_id
    assert recorded["login"][1] == "auth"
    assert recorded["category_config_updated"][0] == current_user_id
    assert recorded["category_config_updated"][1] == "category_config"
    assert recorded["logout"][0] == current_user_id
    assert recorded["logout"][1] == "auth"


def test_admin_audit_review_page_lists_and_filters_events(tmp_path, monkeypatch):
    db_path = tmp_path / "audit_review.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    app_instance = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-audit-review-secret",
            "SKIP_INIT_DB": True,
        },
        auto_init=False,
    )

    with app_instance.app_context():
        conn = sqlite3.connect(db_path)
        common.migrate_auth_schema(conn)
        common.migrate_audit_log_schema(conn)

        admin_user = conn.execute(
            "INSERT INTO users (username, password_hash, is_active) VALUES (?, ?, 1)",
            ("audit-review-admin", generate_password_hash("strong-pass")),
        )
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'audit-review-admin'").fetchone()[0]
        operator_id = conn.execute(
            "INSERT INTO users (username, password_hash, is_active) VALUES (?, ?, 1)",
            ("audit-review-op", generate_password_hash("operator-pass")),
        )
        operator_id = conn.execute("SELECT id FROM users WHERE username = 'audit-review-op'").fetchone()[0]

        admin_role_id = conn.execute("SELECT id FROM roles WHERE name = 'administrator'").fetchone()[0]
        conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (admin_id, admin_role_id))
        conn.execute("INSERT INTO audit_log (user_id, action_type, resource_type, details, created_at) VALUES (?, ?, ?, ?, ?)",
            (admin_id, "login", "auth", json.dumps({"username": "audit-review-admin"}), "2026-08-20 12:00:00"))
        conn.execute("INSERT INTO audit_log (user_id, action_type, resource_type, details, created_at) VALUES (?, ?, ?, ?, ?)",
            (operator_id, "category_config_updated", "category_config", json.dumps({"glicko_k": 20}), "2026-08-21 09:15:00"))
        conn.commit()
        conn.close()

    client = app_instance.test_client()
    conftest.set_admin_session(client, db_path)

    response = client.get("/admin/audit?lang=en")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Audit review" in body
    assert "login" in body
    assert "category_config_updated" in body

    filtered = client.get("/admin/audit?lang=en&user_id=%s&action_type=login" % admin_id)
    assert filtered.status_code == 200
    filtered_body = filtered.get_data(as_text=True)
    assert re.search(r"<td>\s*login\s*</td>", filtered_body)
    assert not re.search(r"<td>\s*category_config_updated\s*</td>", filtered_body)

    searched = client.get(
        "/admin/audit?lang=en&q=glicko_k&date_from=2026-08-21&date_to=2026-08-21"
    )
    searched_body = searched.get_data(as_text=True)
    assert searched.status_code == 200
    assert "category_config_updated" in searched_body
    assert not re.search(r"<td>\s*login\s*</td>", searched_body)

    invalid_user = client.get("/admin/audit?lang=en&user_id=invalid")
    assert invalid_user.status_code == 200


def test_audit_log_prunes_old_rows_and_caps_details(tmp_path, monkeypatch):
    db_path = tmp_path / "audit_retention.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    conn = sqlite3.connect(db_path)
    common.migrate_auth_schema(conn)
    common.migrate_audit_log_schema(conn)
    conn.execute(
        "INSERT INTO audit_log (action_type, resource_type, details, created_at) VALUES (?, ?, ?, ?)",
        ("old_event", "test", "{}", "2020-01-01 00:00:00"),
    )
    conn.commit()

    common.log_admin_action("compact_event", "test", {"payload": "x" * 10000}, conn=conn)

    old_event = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action_type = 'old_event'"
    ).fetchone()[0]
    details = conn.execute(
        "SELECT details FROM audit_log WHERE action_type = 'compact_event'"
    ).fetchone()[0]
    conn.close()

    assert old_event == 0
    assert len(details.encode("utf-8")) <= common.AUDIT_DETAILS_MAX_BYTES


def test_admin_secondary_button_css_uses_visible_text_color():
    css_path = Path("static/css/layout.css")
    css = css_path.read_text(encoding="utf-8")

    index = css.index(".button.secondary")
    block = css[index:index + 250]

    assert "color:" in block
    assert "white" in block.lower()
