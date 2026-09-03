import sqlite3
import smtplib

from werkzeug.security import check_password_hash, generate_password_hash

import config
import routes.admin as admin_routes
import services.common as common
from app import create_app
from config import DEFAULT_RATING, DEFAULT_RD, DEFAULT_VOLATILITY, GLICKO_K, GLICKO_M, TAU
from services.settings_service import DEFAULT_APPLICATION_SETTINGS


def make_account_app(tmp_path, monkeypatch, role_name="operator"):
    db_path = tmp_path / "profile.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))
    app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "profile-test-secret",
            "SKIP_INIT_DB": True,
        },
        auto_init=False,
    )
    conn = sqlite3.connect(db_path)
    common.migrate_auth_schema(conn)
    conn.execute(
        "CREATE TABLE players (id INTEGER PRIMARY KEY, display_name TEXT, rating REAL, games_played INTEGER, active INTEGER)"
    )
    conn.execute(
        "CREATE TABLE category_config (id INTEGER PRIMARY KEY, glicko_k REAL, glicko_m REAL, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE rating_config (id INTEGER PRIMARY KEY, tau REAL, default_rating REAL, default_rd REAL, default_volatility REAL, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO users (username, password_hash, email) VALUES (?, ?, ?)",
        ("profile-user", generate_password_hash("old-password"), "user@example.com"),
    )
    user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO user_roles (user_id, role_id) VALUES (?, (SELECT id FROM roles WHERE name = ?))",
        (user_id, role_name),
    )
    conn.commit()
    conn.close()
    return app, db_path, user_id


def authenticate_client(client, user_id, role_name="operator"):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["user_role"] = role_name


def test_profile_persists_preferences_and_changes_password(tmp_path, monkeypatch):
    app, db_path, user_id = make_account_app(tmp_path, monkeypatch)
    client = app.test_client()
    authenticate_client(client, user_id)

    response = client.post(
        "/admin/profile?lang=en",
        data={
            "email": "new@example.com",
            "language": "pt",
            "theme": "dark",
            "timezone": "America/Bogota",
            "current_password": "old-password",
            "new_password": "new-password",
            "confirm_password": "new-password",
        },
    )
    assert response.status_code == 302

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT email, language, theme, timezone, password_hash FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    assert row[:4] == ("new@example.com", "pt", "dark", "America/Bogota")
    assert check_password_hash(row[4], "new-password")

    with client.session_transaction() as session:
        assert session["user_language"] == "pt"
        assert session["user_theme"] == "dark"

    response = client.get("/admin/profile")
    assert response.status_code == 200
    assert "Meu perfil" in response.get_data(as_text=True)


def test_member_can_view_and_edit_profile(tmp_path, monkeypatch):
    app, db_path, user_id = make_account_app(tmp_path, monkeypatch, role_name="member")
    client = app.test_client()
    authenticate_client(client, user_id, role_name="member")

    response = client.get("/admin/profile?lang=en")
    assert response.status_code == 200

    response = client.post(
        "/admin/profile?lang=en",
        data={
            "email": "member-updated@example.com",
            "language": "en",
            "theme": "dark",
            "timezone": "America/Bogota",
        },
    )
    assert response.status_code == 302

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT email, theme, timezone FROM users WHERE id = ?",
            (user_id,),
        ).fetchone() == ("member-updated@example.com", "dark", "America/Bogota")


def test_profile_timezone_options_display_utc_adjustments(tmp_path, monkeypatch):
    app, _db_path, user_id = make_account_app(tmp_path, monkeypatch)
    client = app.test_client()
    authenticate_client(client, user_id)

    response = client.get("/admin/profile?lang=en")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert '<option value="UTC"' in page and '>UTC</option>' in page
    assert '<option value="America/Bogota"' in page and '>America/Bogota (UTC-5)</option>' in page


def test_profile_logout_button_clears_session_and_redirects(tmp_path, monkeypatch):
    app, _db_path, user_id = make_account_app(tmp_path, monkeypatch)
    client = app.test_client()
    authenticate_client(client, user_id)

    response = client.post(
        "/admin/profile?lang=en",
        data={"logout": "1"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/?lang=en")
    with client.session_transaction() as session:
        assert session.get("user_id") is None

    protected_response = client.get("/admin/profile?lang=en")
    assert protected_response.status_code == 302
    assert "/admin/login" in protected_response.headers["Location"]


def test_anonymous_preferences_are_saved_in_cookies(tmp_path, monkeypatch):
    app, _db_path, _user_id = make_account_app(tmp_path, monkeypatch)
    client = app.test_client()

    response = client.post(
        "/preferences",
        json={"language": "pt", "theme": "dark"},
    )

    assert response.status_code == 200
    assert client.get_cookie("user_language").value == "pt"
    assert client.get_cookie("user_theme").value == "dark"


def test_authenticated_preferences_update_profile_and_session(tmp_path, monkeypatch):
    app, db_path, user_id = make_account_app(tmp_path, monkeypatch)
    client = app.test_client()
    authenticate_client(client, user_id)

    response = client.post(
        "/preferences",
        json={"language": "en", "theme": "dark"},
    )

    assert response.status_code == 200
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT language, theme FROM users WHERE id = ?", (user_id,)
    ).fetchone() == ("en", "dark")
    conn.close()
    with client.session_transaction() as session:
        assert session["user_language"] == "en"
        assert session["user_theme"] == "dark"


def test_forgot_password_response_does_not_reveal_account_existence(tmp_path, monkeypatch):
    app, _db_path, _user_id = make_account_app(tmp_path, monkeypatch)
    client = app.test_client()

    unknown = client.post("/admin/forgot-password?lang=en", data={"email": "missing@example.com"})
    known = client.post("/admin/forgot-password?lang=en", data={"email": "user@example.com"})

    assert unknown.status_code == known.status_code == 200
    assert unknown.get_data(as_text=True).count("If an account exists") == 1
    assert known.get_data(as_text=True).count("If an account exists") == 1


def test_forgot_password_sends_reset_url_and_token_is_single_use(tmp_path, monkeypatch):
    app, db_path, user_id = make_account_app(tmp_path, monkeypatch)
    client = app.test_client()
    sent = []
    monkeypatch.setattr(admin_routes, "send_password_reset_email", lambda recipient, url: sent.append((recipient, url)))

    response = client.post("/admin/forgot-password?lang=en", data={"email": "USER@example.com"})
    assert response.status_code == 200
    assert sent and sent[0][0] == "user@example.com"
    assert "/admin/reset-password/" in sent[0][1]

    token = sent[0][1].split("/admin/reset-password/", 1)[1].split("?", 1)[0]
    response = client.post(
        f"/admin/reset-password/{token}?lang=en",
        data={"new_password": "reset-password", "confirm_password": "reset-password"},
    )
    assert response.status_code == 302
    assert common.reset_password_with_token(token, "another-password") is False

    conn = sqlite3.connect(db_path)
    password_hash = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    conn.close()
    assert check_password_hash(password_hash, "reset-password")


def test_forgot_password_handles_missing_smtp_configuration(tmp_path, monkeypatch):
    app, _db_path, _user_id = make_account_app(tmp_path, monkeypatch)
    client = app.test_client()
    monkeypatch.setattr(common, "MAIL_SERVER", "")

    response = client.post(
        "/admin/forgot-password?lang=en",
        data={"email": "user@example.com"},
    )

    assert response.status_code == 200
    assert "If an account exists" in response.get_data(as_text=True)
    assert "password reset email is not configured" not in response.get_data(as_text=True)


def test_forgot_password_handles_incorrect_smtp_credentials(tmp_path, monkeypatch):
    app, _db_path, _user_id = make_account_app(tmp_path, monkeypatch)
    client = app.test_client()

    def fail_delivery(_recipient, _reset_url):
        raise smtplib.SMTPAuthenticationError(535, b"authentication failed")

    monkeypatch.setattr(admin_routes, "send_password_reset_email", fail_delivery)

    response = client.post(
        "/admin/forgot-password?lang=en",
        data={"email": "user@example.com"},
    )

    assert response.status_code == 200
    assert "If an account exists" in response.get_data(as_text=True)
    assert "authentication failed" not in response.get_data(as_text=True)


def test_admin_setting_pages_reset_to_config_defaults(tmp_path, monkeypatch):
    app, db_path, user_id = make_account_app(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO category_config (id, glicko_k, glicko_m, updated_at) VALUES (1, 99, 999, CURRENT_TIMESTAMP)"
    )
    conn.execute(
        """
        INSERT INTO rating_config
            (id, tau, default_rating, default_rd, default_volatility, updated_at)
        VALUES (1, 0.9, 1900, 300, 0.02, CURRENT_TIMESTAMP)
        """
    )
    conn.commit()
    conn.close()
    client = app.test_client()
    authenticate_client(client, user_id)

    category_response = client.post(
        "/admin/categories?lang=en",
        data={"action": "reset"},
    )
    rating_response = client.post(
        "/admin/ratings?lang=en",
        data={"action": "reset"},
    )

    assert category_response.status_code == 302
    assert rating_response.status_code == 302
    conn = sqlite3.connect(db_path)
    category = conn.execute("SELECT glicko_k, glicko_m FROM category_config WHERE id = 1").fetchone()
    rating = conn.execute(
        "SELECT tau, default_rating, default_rd, default_volatility FROM rating_config WHERE id = 1"
    ).fetchone()
    conn.close()
    assert category == (GLICKO_K, GLICKO_M)
    assert rating == (TAU, DEFAULT_RATING, DEFAULT_RD, DEFAULT_VOLATILITY)


def test_administrator_can_update_and_reset_application_settings(tmp_path, monkeypatch):
    app, db_path, user_id = make_account_app(tmp_path, monkeypatch, role_name="administrator")
    client = app.test_client()
    authenticate_client(client, user_id, role_name="administrator")

    response = client.post(
        "/admin/settings?lang=en",
        data={
            "action": "save",
            "max_login_attempts": "12",
            "login_window_seconds": "90",
            "password_reset_ttl_seconds": "7200",
        },
    )
    assert response.status_code == 302
    conn = sqlite3.connect(db_path)
    settings = conn.execute(
        "SELECT max_login_attempts, login_window_seconds, password_reset_ttl_seconds FROM application_settings"
    ).fetchone()
    conn.close()
    assert settings == (12, 90, 7200)

    response = client.post("/admin/settings?lang=en", data={"action": "reset"})
    assert response.status_code == 302
    conn = common.get_db()
    try:
        settings = conn.execute(
            "SELECT max_login_attempts, login_window_seconds, password_reset_ttl_seconds FROM application_settings"
        ).fetchone()
    finally:
        conn.close()
    assert tuple(settings) == tuple(DEFAULT_APPLICATION_SETTINGS.values())


def test_non_administrator_cannot_edit_application_settings(tmp_path, monkeypatch):
    app, _db_path, user_id = make_account_app(tmp_path, monkeypatch)
    client = app.test_client()
    authenticate_client(client, user_id)

    response = client.get("/admin/settings?lang=en")

    assert response.status_code == 403
