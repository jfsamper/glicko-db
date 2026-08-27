import sqlite3

import conftest
from app import app
import config
import services.common as common
import routes.admin as admin_routes
import routes.public as public_routes
from routes.public import get_public_tournament_status


def test_public_tournament_index_renders_in_supported_languages():
    app.testing = True
    client = app.test_client()

    expected_labels = {
        "en": ("Show drafts", "Hide drafts"),
        "es": ("Mostrar borradores", "Ocultar borradores"),
        "pt": ("Mostrar rascunhos", "Ocultar rascunhos"),
    }

    for language, (show_text, hide_text) in expected_labels.items():
        response = client.get(f"/tournaments?lang={language}")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert show_text in body
        response = client.get(f"/tournaments?lang={language}&show_drafts=1")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert hide_text in body


def test_public_navigation_keeps_reports_link_and_sidebar_login_link():
    app.testing = True
    body = app.test_client().get("/?lang=en").get_data(as_text=True)

    assert 'href="/reports?lang=en"' in body
    assert 'href="/admin/login?lang=en"' in body
    assert "Report results" in body


def test_public_tournament_index_supports_sort_by_name(monkeypatch, tmp_path):
    db_path = tmp_path / "sorted_tournaments.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE tournaments (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            location TEXT,
            rounds INTEGER NOT NULL DEFAULT 1,
            tournament_type TEXT,
            pairing_system TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT,
            begin_date TEXT,
            bye_points REAL,
            absent_points REAL
        );
        INSERT INTO tournaments (id, name, status, created_at, begin_date, tournament_type, pairing_system)
        VALUES
            (1, 'Zulu Open', 'completed', '2026-01-01', '2026-01-10', 'swiss', 'swiss'),
            (2, 'Alpha Cup', 'completed', '2026-02-01', '2026-02-20', 'swiss', 'swiss'),
            (3, 'Beta Masters', 'completed', '2026-03-01', '2026-03-14', 'swiss', 'swiss');
        """
    )
    conn.commit()
    conn.close()

    response = app.test_client().get("/tournaments?lang=en&show_drafts=1&sort=name&order=asc")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert body.index("Alpha Cup") < body.index("Beta Masters")
    assert body.index("Beta Masters") < body.index("Zulu Open")

    app.testing = True
    admin_client = app.test_client()
    conftest.set_admin_session(admin_client, db_path)
    response = admin_client.get("/admin/tournaments?lang=en&sort=name&order=asc")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert body.index("Alpha Cup") < body.index("Beta Masters")
    assert body.index("Beta Masters") < body.index("Zulu Open")


def test_admin_can_update_imported_tournament_status(monkeypatch, tmp_path):
    db_path = tmp_path / "admin_tournament_status.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE tournaments (id INTEGER PRIMARY KEY, status TEXT, source_format TEXT)"
    )
    conn.execute(
        "INSERT INTO tournaments (id, status, source_format) VALUES (5, 'draft', 'OpenGotha XML')"
    )
    conn.commit()
    conn.close()

    app.testing = True
    client = app.test_client()
    conftest.set_admin_session(client, db_path)

    response = client.post(
        "/admin/tournaments/5/status?lang=en",
        data={"status": "active"},
    )

    assert response.status_code == 302
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT status FROM tournaments WHERE id = 5").fetchone()[0] == "active"
    conn.close()


def test_admin_tournament_delete_uses_explicit_modal(monkeypatch, tmp_path):
    db_path = tmp_path / "admin_tournament_delete_modal.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE tournaments (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            rounds INTEGER NOT NULL DEFAULT 1,
            pairing_system TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT,
            begin_date TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO tournaments (id, name, pairing_system) VALUES (1, 'Spring Cup', 'swiss')"
    )
    conn.commit()
    conn.close()

    client = app.test_client()
    conftest.set_admin_session(client, db_path)

    response = client.get("/admin/tournaments?lang=en")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="delete-tournament-dialog"' in body
    assert 'data-delete-tournament' in body
    assert 'data-tournament-name="Spring Cup"' in body
    assert "Delete tournament" in body
    assert "Cancel" in body
    assert "confirm(" not in body


def test_public_matches_support_sort_by_white_player(monkeypatch, tmp_path):
    db_path = tmp_path / "sorted_matches.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE players (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL);
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            event TEXT,
            notes TEXT,
            round_number INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO players (id, display_name) VALUES (1, 'Charlie'), (2, 'Bob'), (3, 'Alice');
        INSERT INTO matches (id, match_date, white_player_id, black_player_id, result, event, notes, round_number)
        VALUES
            (1, '2026-08-20', 1, 2, '1-0', 'Charlie vs Bob', 'Round 1', 1),
            (2, '2026-08-21', 2, 3, '0-1', 'Bob vs Alice', 'Round 2', 1),
            (3, '2026-08-19', 3, 1, '1/2-1/2', 'Alice vs Charlie', 'Round 3', 1);
        """
    )
    conn.commit()
    conn.close()

    response = app.test_client().get("/matches?lang=en&sort=white&order=asc&page=1&page_size=10")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert body.index("Alice vs Charlie") < body.index("Bob vs Alice")
    assert body.index("Bob vs Alice") < body.index("Charlie vs Bob")


def test_public_matches_order_same_day_rounds_before_pagination(monkeypatch, tmp_path):
    db_path = tmp_path / "same_day_matches.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE players (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL);
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            event TEXT,
            notes TEXT,
            round_number INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO players (id, display_name) VALUES (1, 'Alice'), (2, 'Bob');
        INSERT INTO matches
            (id, match_date, white_player_id, black_player_id, result, event, notes, round_number)
        VALUES
            (1, '2026-08-17', 1, 2, '1-0', 'same-day-round-1', 'Round 1', 1),
            (2, '2026-08-17', 1, 2, '1-0', 'same-day-round-2', 'Round 2', 2),
            (3, '2026-08-17', 1, 2, '1-0', 'same-day-round-3', 'Round 3', 3),
            (4, '2026-08-17', 1, 2, '1-0', 'same-day-unknown', 'Unparsed note', 0);
        """
    )
    conn.commit()
    conn.close()

    response = app.test_client().get("/matches?lang=en&page=1&page_size=3")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert body.index("same-day-round-3") < body.index("same-day-round-2")
    assert body.index("same-day-round-2") < body.index("same-day-round-1")
    assert "<td>3</td>" in body
    assert "same-day-unknown" not in body

    response = app.test_client().get("/matches?lang=en&page=2&page_size=3")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "same-day-unknown" in body
    assert "<td>Unparsed note</td>" in body

    with app.test_client() as admin_client:
        conftest.set_admin_session(admin_client, db_path)
        response = admin_client.get("/admin/matches?lang=en&page=1&page_size=3")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert body.index("same-day-round-3") < body.index("same-day-round-2")
    assert body.index("same-day-round-2") < body.index("same-day-round-1")
    assert "same-day-unknown" not in body


def test_admin_edit_match_preserves_string_note_when_stats_refresh_fails(monkeypatch, tmp_path):
    db_path = tmp_path / "admin_match_note.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))
    monkeypatch.setattr(
        admin_routes,
        "refresh_stats",
        lambda: (_ for _ in ()).throw(sqlite3.DatabaseError("database disk image is malformed")),
    )

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE players (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL);
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            event TEXT,
            notes TEXT,
            round_number INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO players (id, display_name) VALUES (1, 'Alice'), (2, 'Bob');
        INSERT INTO matches
            (id, match_date, white_player_id, black_player_id, result, event, notes)
        VALUES (203768, '2026-08-17', 1, 2, '1-0', 'Test', 'Round 2');
        """
    )
    conn.commit()
    conn.close()

    app.testing = True
    client = app.test_client()
    conftest.set_admin_session(client, db_path)

    response = client.post(
        "/admin/matches/edit?id=203768&lang=en",
        data={
            "match_date": "2026-08-17",
            "white_player_id": "1",
            "black_player_id": "2",
            "result": "1-0",
            "event": "Test",
            "notes": "Adjourned game",
        },
    )

    assert response.status_code == 302
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT notes FROM matches WHERE id = 203768").fetchone()[0] == "Adjourned game"
    conn.close()


def test_public_tournament_status_keeps_unprocessed_imports_as_draft():
    imported = {"source_format": "OpenGotha XML", "status": "draft"}
    assert get_public_tournament_status(imported) == "draft"


def test_public_tournament_index_hides_drafts_by_default(monkeypatch, tmp_path):
    db_path = tmp_path / "public_tournament_list.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE tournaments (
            id INTEGER PRIMARY KEY,
            name TEXT,
            status TEXT,
            begin_date TEXT,
            created_at TEXT,
            source_format TEXT
        );
        CREATE TABLE tournament_participants (
            tournament_id INTEGER,
            player_id INTEGER
        );
        CREATE TABLE tournament_pending_players (
            id INTEGER PRIMARY KEY,
            tournament_id INTEGER,
            display_name TEXT
        );
        INSERT INTO tournaments (id, name, status, begin_date, created_at, source_format)
        VALUES
            (1, 'Spring Open', 'active', '2026-04-01', '2026-04-01 00:00:00', NULL),
            (2, 'Draft import preview', 'draft', '2026-03-01', '2026-03-01 00:00:00', 'OpenGotha XML');
        """
    )
    conn.close()

    app.testing = True
    client = app.test_client()
    response = client.get("/tournaments?lang=en")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Spring Open" in body
    assert "Draft import preview" not in body

    response = client.get("/tournaments?lang=en&show_drafts=1")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Draft import preview" in body


def test_public_tournament_status_maps_active_and_preserves_canceled():
    assert get_public_tournament_status(
        {"source_format": None, "status": "active"},
    ) == "active"
    assert get_public_tournament_status(
        {"source_format": None, "status": "canceled"},
    ) == "canceled"


def test_app_sets_secure_cookie_flags():
    assert app.config.get("SESSION_COOKIE_SECURE") is True
    assert app.config.get("SESSION_COOKIE_HTTPONLY") is True
    assert app.config.get("SESSION_COOKIE_SAMESITE") == "Lax"


def test_public_tournament_detail_uses_public_status_helper(monkeypatch, tmp_path):
    db_path = tmp_path / "public_tournament_detail.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            rating REAL,
            slug TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            short_name TEXT,
            location TEXT,
            begin_date TEXT,
            end_date TEXT,
            rounds INTEGER NOT NULL DEFAULT 1,
            tournament_type TEXT NOT NULL DEFAULT 'swiss',
            pairing_system TEXT NOT NULL DEFAULT 'swiss',
            bye_points REAL NOT NULL DEFAULT 1,
            absent_points REAL NOT NULL DEFAULT 0,
            placement_criteria TEXT NOT NULL DEFAULT 'NBW,SOS,SOSOS',
            status TEXT NOT NULL DEFAULT 'draft',
            source_format TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE tournament_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            round_number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled'
        );
        CREATE TABLE tournament_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            seed_rating REAL NOT NULL DEFAULT 0,
            seed_rank INTEGER NOT NULL DEFAULT 0,
            category TEXT NOT NULL DEFAULT '',
            initial_score REAL NOT NULL DEFAULT 0,
            acceleration REAL NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            received_bye INTEGER NOT NULL DEFAULT 0,
            mc_seeds_calculated INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE tournament_pairings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER NOT NULL,
            board_number INTEGER NOT NULL DEFAULT 1,
            white_player_id INTEGER,
            black_player_id INTEGER,
            white_player_name TEXT,
            black_player_name TEXT,
            result TEXT,
            is_bye INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE tournament_pending_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            display_name TEXT NOT NULL,
            suggested_name TEXT,
            rating REAL NOT NULL DEFAULT 0,
            rank INTEGER NOT NULL DEFAULT 0,
            category TEXT NOT NULL DEFAULT '',
            source_key TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE tournament_round_players (
            round_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('paired', 'bye', 'absent')),
            UNIQUE(round_id, player_id)
        );
        """
    )
    conn.execute(
        "INSERT INTO tournaments (id, name, status, source_format) VALUES (?, ?, ?, ?)",
        (5, "Spring Open", "draft", "OpenGotha XML"),
    )
    conn.commit()

    captured = {}

    def fake_render_template(*args, **kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(public_routes, "render_template", fake_render_template)
    monkeypatch.setattr(public_routes, "get_db", lambda: conn)

    app.testing = True
    client = app.test_client()
    response = client.get("/tournaments/5?lang=en")

    assert response.status_code == 200
    assert response.data == b"ok"
    assert captured["tournament"]["public_status"] == "draft"
    conn.close()


def test_get_team_members_uses_single_targeted_lookup(monkeypatch):
    import routes.public as public_routes

    monkeypatch.setattr(
        public_routes,
        "TEAM_ROLE_ALIASES",
        {
            "Presidente": ["Carlos Cruz", "cruz, carlos"],
            "Secretario": ["Carlos Gaitan", "gaitan, carlos"],
            "Tesorero": ["Juan Rivera", "rivera, juan"],
        },
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT
        );
        INSERT INTO players (id, display_name) VALUES (1, 'Carlos Cruz');
        INSERT INTO players (id, display_name) VALUES (2, 'Carlos Gaitan');
        INSERT INTO players (id, display_name) VALUES (3, 'Juan Rivera');
        """
    )

    class GuardedConnection:
        def __init__(self, wrapped):
            self._wrapped = wrapped
            self.executed_sql = []

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def execute(self, sql, params=()):
            sql_text = str(sql).lower()
            self.executed_sql.append(sql_text)
            if "where 1=0" in sql_text:
                raise AssertionError("full-table dummy query should not be used")
            return self._wrapped.execute(sql, params)

    guarded = GuardedConnection(conn)
    members = public_routes.get_team_members(guarded)

    assert {member["role"] for member in members} == {"Presidente", "Secretario", "Tesorero"}
    assert len(guarded.executed_sql) == 1
    conn.close()


def test_public_tournament_detail_renders_draft_round_results(monkeypatch, tmp_path):
    db_path = tmp_path / "draft_tournament.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            display_name TEXT,
            rating REAL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '', short_name TEXT, location TEXT,
            begin_date TEXT, end_date TEXT, rounds INTEGER NOT NULL DEFAULT 1,
            tournament_type TEXT NOT NULL DEFAULT 'swiss',
            pairing_system TEXT NOT NULL DEFAULT 'swiss',
            bye_points REAL NOT NULL DEFAULT 1,
            absent_points REAL NOT NULL DEFAULT 0,
            placement_criteria TEXT NOT NULL DEFAULT 'NBW,SOS,SOSOS',
            status TEXT NOT NULL DEFAULT 'draft', source_format TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE tournament_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            round_number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled'
        );
        CREATE TABLE tournament_pairings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER NOT NULL,
            board_number INTEGER NOT NULL DEFAULT 1,
            white_player_id INTEGER,
            black_player_id INTEGER,
            white_player_name TEXT,
            black_player_name TEXT,
            result TEXT,
            is_bye INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE tournament_round_players (
            round_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'paired'
        );
        CREATE TABLE tournament_pending_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            display_name TEXT NOT NULL,
            suggested_name TEXT,
            rating REAL NOT NULL DEFAULT 0,
            rank INTEGER NOT NULL DEFAULT 0,
            category TEXT NOT NULL DEFAULT '',
            source_key TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE tournament_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            seed_rating REAL NOT NULL DEFAULT 0,
            seed_rank INTEGER NOT NULL DEFAULT 0,
            category TEXT NOT NULL DEFAULT '',
            initial_score REAL NOT NULL DEFAULT 0,
            acceleration REAL NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            received_bye INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute(
        "INSERT INTO tournaments (id, name, rounds, status) VALUES (?, ?, ?, 'draft')",
        (1, "Draft import preview", 1),
    )
    conn.execute(
        "INSERT INTO tournament_rounds (id, tournament_id, round_number, status) VALUES (?, ?, ?, 'completed')",
        (11, 1, 1),
    )
    conn.execute(
        "INSERT INTO tournament_pairings (round_id, board_number, white_player_name, black_player_name, result, is_bye) VALUES (?, ?, ?, ?, ?, ?)",
        (11, 1, "Alice Example", "Bob Example", "1-0", 0),
    )
    conn.execute(
        "INSERT INTO tournament_pending_players (tournament_id, display_name, rating, rank) VALUES (?, ?, ?, ?)",
        (1, "Alice Example", 1500, 1),
    )
    conn.execute(
        "INSERT INTO tournament_pending_players (tournament_id, display_name, rating, rank) VALUES (?, ?, ?, ?)",
        (1, "Bob Example", 1400, 2),
    )
    conn.commit()
    conn.close()

    client = app.test_client()
    response = client.get("/tournaments/1?lang=en")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Alice Example" in body
    assert "Bob Example" in body


def test_admin_matches_page_supports_pagination_controls():
    app.testing = True
    client = app.test_client()
    conftest.set_admin_session(client)

    response = client.get("/admin/matches?lang=en&page=1&page_size=2")

    assert response.status_code == 200
    assert "Page 1" in response.get_data(as_text=True)
    assert "Next" in response.get_data(as_text=True)
