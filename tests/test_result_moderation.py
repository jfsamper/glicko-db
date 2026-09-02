import sqlite3

import config
import app as app_module
import routes.admin as admin_routes
import services.common as common
import services.recaptcha as recaptcha
from app import create_app


def make_moderation_app(tmp_path, monkeypatch):
    db_path = tmp_path / "result_moderation.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))
    monkeypatch.setattr(app_module, "DB_PATH", str(db_path))
    application = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "result-moderation-secret",
        },
        auto_init=True,
    )
    conn = sqlite3.connect(db_path)
    try:
        player_ids = []
        for name in ("Member Player", "Opponent Player"):
            first_name, last_name = name.split()
            player_ids.append(
                conn.execute(
                    """
                    INSERT INTO players
                        (first_name, last_name, display_name, slug, active, rating, rd, volatility)
                    VALUES (?, ?, ?, ?, 1, 1500, 350, 0.06)
                    """,
                    (first_name, last_name, name, name.lower().replace(" ", "-")),
                ).lastrowid
            )
        conn.commit()
    finally:
        conn.close()
    return application, db_path, player_ids


def authenticate(client, user_id, role):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["user_role"] = role


def test_registration_creates_member_account(tmp_path, monkeypatch):
    application, db_path, _ = make_moderation_app(tmp_path, monkeypatch)
    client = application.test_client()

    response = client.post(
        "/admin/register?lang=en",
        data={
            "username": "new-member",
            "email": "member@example.com",
            "password": "member-password",
            "confirm_password": "member-password",
        },
    )

    assert response.status_code == 302
    with sqlite3.connect(db_path) as conn:
        role = conn.execute(
            """
            SELECT r.name FROM users u
            JOIN user_roles ur ON ur.user_id = u.id
            JOIN roles r ON r.id = ur.role_id
            WHERE u.username = 'new-member'
            """
        ).fetchone()[0]
    assert role == "member"


def test_registration_requires_recaptcha_when_configured(tmp_path, monkeypatch):
    application, db_path, _ = make_moderation_app(tmp_path, monkeypatch)
    monkeypatch.setattr(recaptcha, "RECAPTCHA_SECRET_KEY", "test-secret")
    monkeypatch.setattr(recaptcha, "RECAPTCHA_SITE_KEY", "test-site-key")
    client = application.test_client()

    response = client.post(
        "/admin/register?lang=en",
        data={
            "username": "blocked-member",
            "email": "blocked@example.com",
            "password": "member-password",
            "confirm_password": "member-password",
        },
    )

    assert response.status_code == 200
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT 1 FROM users WHERE username = 'blocked-member'"
        ).fetchone() is None


def test_recaptcha_verifier_checks_action_score_and_hostname(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc_value, _traceback):
            return False

        def read(self):
            import json

            return json.dumps(self.payload).encode("utf-8")

    monkeypatch.setattr(recaptcha, "RECAPTCHA_SECRET_KEY", "test-secret")
    monkeypatch.setattr(recaptcha, "RECAPTCHA_SITE_KEY", "test-site-key")
    monkeypatch.setattr(recaptcha, "RECAPTCHA_MIN_SCORE", 0.5)
    monkeypatch.setattr(recaptcha, "RECAPTCHA_EXPECTED_HOSTNAME", "example.com")

    monkeypatch.setattr(
        recaptcha,
        "urlopen",
        lambda _request, timeout: FakeResponse(
            {"success": True, "action": "register", "score": 0.9, "hostname": "example.com"}
        ),
    )
    assert recaptcha.verify_recaptcha("token", "register") is True

    for payload in (
        {"success": False, "action": "register", "score": 0.9, "hostname": "example.com"},
        {"success": True, "action": "login", "score": 0.9, "hostname": "example.com"},
        {"success": True, "action": "register", "score": 0.4, "hostname": "example.com"},
        {"success": True, "action": "register", "score": 0.9, "hostname": "other.example.com"},
    ):
        monkeypatch.setattr(recaptcha, "urlopen", lambda _request, timeout, payload=payload: FakeResponse(payload))
        assert recaptcha.verify_recaptcha("token", "register") is False


def test_member_can_submit_only_for_linked_player(tmp_path, monkeypatch):
    application, db_path, player_ids = make_moderation_app(tmp_path, monkeypatch)
    member_id = common.create_user_account(
        "member",
        "member-password",
        role_name="member",
        player_id=player_ids[0],
    )
    client = application.test_client()
    authenticate(client, member_id, "member")

    assert client.get("/admin").status_code == 403
    assert client.get("/admin/result-submissions").status_code == 403
    report_response = client.get("/admin/report-results?lang=en")
    assert report_response.status_code == 200
    report_body = report_response.get_data(as_text=True)
    assert "member (Member Player)" in report_body
    assert "My color" in report_body
    assert "Opponent color" not in report_body
    assert report_body.index("handicap_stones") < report_body.index('id="result"')
    assert 'data-player-name="Opponent Player"' in report_body
    with sqlite3.connect(db_path) as conn:
        baseline_match_count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]

    response = client.post(
        "/admin/report-results?lang=en",
        data={
            "match_date": "2026-09-01",
            "opponent_player_id": str(player_ids[1]),
            "color": "white",
            "result": "1-0",
            "event": "Club night",
            "notes": "Round 1",
            "handicap_stones": "0",
        },
    )

    assert response.status_code == 302
    with sqlite3.connect(db_path) as conn:
        submission = conn.execute(
            "SELECT white_player_id, black_player_id, status FROM result_submissions"
        ).fetchone()
        match_count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    assert submission == (player_ids[0], player_ids[1], "pending")
    assert match_count == baseline_match_count


def test_staff_approval_materializes_pending_result(tmp_path, monkeypatch):
    application, db_path, player_ids = make_moderation_app(tmp_path, monkeypatch)
    member_id = common.create_user_account(
        "member",
        "member-password",
        role_name="member",
        player_id=player_ids[0],
    )
    staff_id = common.create_user_account("operator", "operator-password", role_name="operator")
    conn = sqlite3.connect(db_path)
    try:
        submission_id = conn.execute(
            """
            INSERT INTO result_submissions
                (submitted_by_user_id, match_date, white_player_id, black_player_id, result, event, notes)
            VALUES (?, '2026-09-01', ?, ?, '0-1', 'Club night', 'Round 1')
            """,
            (member_id, player_ids[0], player_ids[1]),
        ).lastrowid
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(admin_routes, "refresh_stats", lambda: None)
    monkeypatch.setattr(admin_routes, "mark_dirty", lambda _date: None)
    monkeypatch.setattr(admin_routes, "update_from_latest_snapshot", lambda: None)
    client = application.test_client()
    authenticate(client, staff_id, "operator")

    response = client.post(
        f"/admin/result-submissions/{submission_id}/approve?lang=en",
        data={"review_notes": "Verified by director"},
    )

    assert response.status_code == 302
    with sqlite3.connect(db_path) as conn:
        submission = conn.execute(
            "SELECT status, reviewed_by_user_id FROM result_submissions WHERE id = ?",
            (submission_id,),
        ).fetchone()
        match = conn.execute(
            "SELECT white_player_id, black_player_id, result, event FROM matches WHERE event = 'Club night'"
        ).fetchone()
    assert submission == ("approved", staff_id)
    assert match == (player_ids[0], player_ids[1], "0-1", "Club night")


def test_auth_migration_adds_member_to_legacy_role_constraint(tmp_path):
    db_path = tmp_path / "legacy_roles.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE CHECK(name IN ('administrator', 'tournament_director', 'operator')),
            description TEXT
        );
        INSERT INTO roles (name) VALUES ('administrator');
        """
    )
    common.migrate_auth_schema(conn)
    assert conn.execute("SELECT id FROM roles WHERE name = 'member'").fetchone() is not None
    conn.close()


def test_result_approval_codes_are_expiring_and_single_use(tmp_path, monkeypatch):
    application, db_path, player_ids = make_moderation_app(tmp_path, monkeypatch)
    member_id = common.create_user_account(
        "member",
        "member-password",
        role_name="member",
        player_id=player_ids[0],
    )
    conn = common.get_db()
    try:
        submission_id = conn.execute(
            """
            INSERT INTO result_submissions
                (submitted_by_user_id, match_date, white_player_id, black_player_id, result)
            VALUES (?, '2026-09-01', ?, ?, '1-0')
            """,
            (member_id, player_ids[0], player_ids[1]),
        ).lastrowid
        expired_submission_id = conn.execute(
            """
            INSERT INTO result_submissions
                (submitted_by_user_id, match_date, white_player_id, black_player_id, result)
            VALUES (?, '2026-09-02', ?, ?, '0-1')
            """,
            (member_id, player_ids[0], player_ids[1]),
        ).lastrowid
        conn.commit()
    finally:
        conn.close()

    code = common.create_result_approval_code(submission_id)
    expired_code = common.create_result_approval_code(expired_submission_id)
    conn = common.get_db()
    try:
        conn.execute(
            "UPDATE result_submissions SET approval_code_expires_at = '2000-01-01 00:00:00' WHERE id = ?",
            (expired_submission_id,),
        )
        conn.commit()
    finally:
        conn.close()

    assert common.consume_result_approval_code(code) == submission_id
    assert common.consume_result_approval_code(code) is None
    assert common.consume_result_approval_code(expired_code) is None
