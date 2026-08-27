import sqlite3
import tempfile
from pathlib import Path

import conftest
from app import app
from config import GLICKO_M
import routes.public as public_routes
import services.player_service as player_service
from services.player_service import parse_rating_filter


def test_parse_rating_filter_rejects_malformed_and_non_finite_values():
    assert parse_rating_filter("1450.5") == 1450.5
    assert parse_rating_filter("not-a-rating") is None
    assert parse_rating_filter("NaN") is None
    assert parse_rating_filter("Infinity") is None


def test_ensure_player_accepts_lookup_cache_for_existing_players():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            slug TEXT,
            rating REAL,
            initial_rating REAL,
            active INTEGER
        )
        """
    )
    conn.execute(
        "INSERT INTO players (display_name, slug, rating, initial_rating, active) VALUES (?, ?, ?, ?, ?)",
        ("Alice", "alice", 1500.0, 1500.0, 1),
    )
    conn.commit()

    lookup = {"alice": 1}
    assert player_service.ensure_player(conn, "Alice", rating=1600.0, player_lookup=lookup) == 1


def test_ensure_player_normalizes_non_positive_import_ratings():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            display_name TEXT,
            country TEXT,
            club TEXT,
            slug TEXT,
            rating REAL,
            initial_rating REAL,
            rd REAL,
            volatility REAL,
            active INTEGER
        )
        """
    )

    player_id = player_service.ensure_player(
        conn,
        "Imported Player",
        rating=0,
        initial_rating=0,
    )

    row = conn.execute(
        "SELECT rating, initial_rating FROM players WHERE id = ?",
        (player_id,),
    ).fetchone()
    assert row["rating"] == 1500.0
    assert row["initial_rating"] == 1500.0

    low_rating_id = player_service.ensure_player(
        conn,
        "Low Rated Player",
        rating=300,
        initial_rating=300,
    )
    low_rating = conn.execute(
        "SELECT rating, initial_rating FROM players WHERE id = ?",
        (low_rating_id,),
    ).fetchone()
    assert low_rating["rating"] == GLICKO_M
    assert low_rating["initial_rating"] == GLICKO_M


def test_load_rankings_ignores_invalid_rating_bounds(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            rating REAL,
            wins INTEGER,
            losses INTEGER,
            draws INTEGER,
            games_played INTEGER,
            active INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT,
            white_player_id INTEGER,
            black_player_id INTEGER,
            result TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO players VALUES (1, 'Alice', 1500, 2, 1, 0, 3, 1)"
    )

    monkeypatch.setattr(player_service, "get_db", lambda: conn)
    monkeypatch.setattr(
        player_service,
        "get_category_config",
        lambda: {"glicko_k": 16.6, "glicko_m": 338},
    )

    rankings = player_service.load_rankings({
        "glicko_min": "not-a-rating",
        "glicko_max": "NaN",
    })

    assert [player["display_name"] for player in rankings] == ["Alice"]


def test_load_rankings_supports_name_sort(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "players_sort.db"

        def connect_db():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        conn = connect_db()
        conn.execute(
            """
            CREATE TABLE players (
                id INTEGER PRIMARY KEY,
                display_name TEXT,
                rating REAL,
                wins INTEGER,
                losses INTEGER,
                draws INTEGER,
                games_played INTEGER,
                active INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE matches (
                id INTEGER PRIMARY KEY,
                match_date TEXT,
                white_player_id INTEGER,
                black_player_id INTEGER,
                result TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO players (id, display_name, rating, wins, losses, draws, games_played, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "Charlie", 1600, 5, 1, 0, 6, 1),
                (2, "Alice", 1500, 3, 2, 1, 6, 1),
                (3, "Bob", 1700, 6, 0, 0, 6, 1),
            ],
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(player_service, "get_db", connect_db)
        monkeypatch.setattr(
            player_service,
            "get_category_config",
            lambda: {"glicko_k": 16.6, "glicko_m": 338},
        )

        rankings = player_service.load_rankings({"sort": "name", "order": "asc", "page_size": 10})
        assert [player["display_name"] for player in rankings] == ["Alice", "Bob", "Charlie"]

        rankings_desc = player_service.load_rankings({"sort": "name", "order": "desc", "page_size": 10})
        assert [player["display_name"] for player in rankings_desc] == ["Charlie", "Bob", "Alice"]


def test_recent_form_only_includes_players_with_matches_in_last_90_days(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "players.db"

        def connect_db():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        conn = connect_db()
        conn.execute(
            """
            CREATE TABLE players (
                id INTEGER PRIMARY KEY,
                display_name TEXT,
                rating REAL,
                wins INTEGER,
                losses INTEGER,
                draws INTEGER,
                games_played INTEGER,
                active INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE matches (
                id INTEGER PRIMARY KEY,
                match_date TEXT,
                white_player_id INTEGER,
                black_player_id INTEGER,
                result TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO players (id, display_name, rating, wins, losses, draws, games_played, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "Alice", 1500, 1, 0, 0, 1, 1),
                (2, "Bob", 1500, 0, 1, 0, 1, 1),
                (3, "Charlie", 1500, 0, 1, 0, 1, 1),
            ],
        )
        recent_match_date = conn.execute("SELECT date('now', '-10 days')").fetchone()[0]
        old_match_date = conn.execute("SELECT date('now', '-120 days')").fetchone()[0]
        conn.executemany(
            "INSERT INTO matches (id, match_date, white_player_id, black_player_id, result) VALUES (?, ?, ?, ?, ?)",
            [
                (1, recent_match_date, 1, 2, "1-0"),
                (2, old_match_date, 3, 2, "0-1"),
            ],
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(player_service, "get_db", connect_db)
        monkeypatch.setattr(
            player_service,
            "get_category_config",
            lambda: {"glicko_k": 16.6, "glicko_m": 338},
        )

        rankings = player_service.load_rankings({"page_size": 10})
        recent_players = [player["display_name"] for player in rankings if player["recent_results"]]

        assert recent_players == ["Alice", "Bob"]


def test_rankings_recent_form_hides_players_without_recent_matches(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "players.db"

        def connect_db():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        conn = connect_db()
        conn.execute(
            """
            CREATE TABLE players (
                id INTEGER PRIMARY KEY,
                display_name TEXT,
                rating REAL,
                wins INTEGER,
                losses INTEGER,
                draws INTEGER,
                games_played INTEGER,
                active INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE matches (
                id INTEGER PRIMARY KEY,
                match_date TEXT,
                white_player_id INTEGER,
                black_player_id INTEGER,
                result TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO players (id, display_name, rating, wins, losses, draws, games_played, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "Alice", 1500, 1, 0, 0, 1, 1),
                (2, "Bob", 1500, 0, 1, 0, 1, 1),
                (3, "Charlie", 1500, 0, 0, 0, 0, 1),
            ],
        )
        recent_match_date = conn.execute("SELECT date('now', '-10 days')").fetchone()[0]
        old_match_date = conn.execute("SELECT date('now', '-120 days')").fetchone()[0]
        conn.executemany(
            "INSERT INTO matches (id, match_date, white_player_id, black_player_id, result) VALUES (?, ?, ?, ?, ?)",
            [
                (1, recent_match_date, 1, 2, "1-0"),
                (2, old_match_date, 3, 2, "0-1"),
            ],
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(player_service, "get_db", connect_db)
        monkeypatch.setattr(
            player_service,
            "get_category_config",
            lambda: {"glicko_k": 16.6, "glicko_m": 338},
        )

        app.testing = True
        client = app.test_client()
        response = client.get("/rankings")
        html = response.get_data(as_text=True)

        assert "<strong>Alice</strong>" in html
        assert "<strong>Bob</strong>" in html
        assert "<strong>Charlie</strong>" not in html


def test_load_rankings_supports_last_active_filter(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "players.db"

        def connect_db():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        conn = connect_db()
        conn.execute(
            """
            CREATE TABLE players (
                id INTEGER PRIMARY KEY,
                display_name TEXT,
                rating REAL,
                wins INTEGER,
                losses INTEGER,
                draws INTEGER,
                games_played INTEGER,
                active INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE matches (
                id INTEGER PRIMARY KEY,
                match_date TEXT,
                white_player_id INTEGER,
                black_player_id INTEGER,
                result TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO players (id, display_name, rating, wins, losses, draws, games_played, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "Alice", 1500, 1, 0, 0, 1, 1),
                (2, "Bob", 1500, 0, 1, 0, 1, 1),
                (3, "Charlie", 1500, 0, 1, 0, 1, 1),
            ],
        )
        recent_match_date = conn.execute("SELECT date('now', '-10 days')").fetchone()[0]
        old_match_date = conn.execute("SELECT date('now', '-400 days')").fetchone()[0]
        conn.executemany(
            "INSERT INTO matches (id, match_date, white_player_id, black_player_id, result) VALUES (?, ?, ?, ?, ?)",
            [
                (1, recent_match_date, 1, 3, "1-0"),
                (2, old_match_date, 2, 3, "0-1"),
            ],
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(player_service, "get_db", connect_db)
        monkeypatch.setattr(
            player_service,
            "get_category_config",
            lambda: {"glicko_k": 16.6, "glicko_m": 338},
        )

        rankings = player_service.load_rankings({"last_active": "365"})
        assert [player["display_name"] for player in rankings] == ["Alice", "Charlie"]

        assert player_service.count_rankings({"last_active": "365"}) == 2


def test_public_matches_route_hides_pagination_for_ten_or_fewer_items(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "players.db"

        def connect_db():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        conn = connect_db()
        conn.execute(
            """
            CREATE TABLE players (
                id INTEGER PRIMARY KEY,
                display_name TEXT,
                rating REAL,
                wins INTEGER,
                losses INTEGER,
                draws INTEGER,
                games_played INTEGER,
                active INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE matches (
                id INTEGER PRIMARY KEY,
                match_date TEXT,
                white_player_id INTEGER,
                black_player_id INTEGER,
                result TEXT,
                event TEXT,
                notes TEXT,
                round_number INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.executemany(
            "INSERT INTO players (id, display_name, rating, wins, losses, draws, games_played, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "Player One", 1500, 1, 0, 0, 1, 1),
                (2, "Player Two", 1500, 1, 0, 0, 1, 1),
                (3, "Player Three", 1500, 1, 0, 0, 1, 1),
            ],
        )
        conn.executemany(
            "INSERT INTO matches (id, match_date, white_player_id, black_player_id, result, event, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "2026-06-01", 1, 2, "1-0", "Event 1", "Round 1"),
                (2, "2026-06-02", 2, 3, "1-0", "Event 2", "Round 2"),
                (3, "2026-06-03", 1, 3, "0-1", "Event 3", "Round 3"),
                (4, "2026-06-04", 2, 1, "1/2-1/2", "Event 4", "Round 4"),
                (5, "2026-06-05", 3, 1, "1-0", "Event 5", "Round 5"),
            ],
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(public_routes, "get_db", connect_db)
        app.testing = True
        client = app.test_client()

        response = client.get("/matches?page=1&page_size=2")

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "Event 5" in html
        assert "Event 4" in html
        assert "Página 1" not in html

def test_player_profile_hides_history_sections_when_player_has_no_matches(monkeypatch):
    monkeypatch.setattr(
        public_routes,
        "load_player",
        lambda *args, **kwargs: {
            "player": {"id": 1, "display_name": "New Player", "rating": 1500, "games_played": 0},
            "matches": [],
            "total_matches": 0,
            "page": 1,
            "page_size": 25,
            "stats": {},
            "rating_history": [],
            "rating_chart": {"points": []},
            "opponent_records": [],
            "badges": [],
            "season": "",
            "profile_seasons": [],
            "tournaments": [],
            "total_tournaments": 0,
            "tournament_pagination": {},
        },
    )
    monkeypatch.setattr(
        public_routes,
        "get_category_config",
        lambda: {"glicko_k": 16.6, "glicko_m": 338},
    )
    app.testing = True

    response = app.test_client().get("/player/view?id=1&lang=en")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert public_routes.TRANSLATIONS["en"]["no_rating_history"] in html
    assert "profile-season" not in html
    assert "Tournament overview" not in html
    assert "Match history" not in html
    assert "Rating history" not in html


def test_load_rankings_supports_fts_search_and_pagination(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "players.db"

        def connect_db():
            return sqlite3.connect(db_path)

        conn = connect_db()
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE players (
                id INTEGER PRIMARY KEY,
                display_name TEXT,
                rating REAL,
                wins INTEGER,
                losses INTEGER,
                draws INTEGER,
                games_played INTEGER,
                active INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE matches (
                id INTEGER PRIMARY KEY,
                match_date TEXT,
                white_player_id INTEGER,
                black_player_id INTEGER,
                result TEXT
            )
            """
        )
        conn.execute(
            "CREATE VIRTUAL TABLE players_fts USING fts5(id UNINDEXED, display_name, rating, wins, losses, draws, games_played, active, content='players', content_rowid='id')"
        )
        conn.executemany(
            "INSERT INTO players (id, display_name, rating, wins, losses, draws, games_played, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "Juan Samper", 1700, 10, 1, 0, 11, 1),
                (2, "Juan Soler", 1650, 9, 2, 0, 11, 1),
                (3, "Camilo Acuna", 1600, 5, 6, 0, 11, 1),
            ],
        )
        conn.executemany(
            "INSERT INTO players_fts (rowid, display_name, rating, wins, losses, draws, games_played, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "Juan Samper", 1700, 10, 1, 0, 11, 1),
                (2, "Juan Soler", 1650, 9, 2, 0, 11, 1),
                (3, "Camilo Acuna", 1600, 5, 6, 0, 11, 1),
            ],
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(player_service, "get_db", connect_db)
        monkeypatch.setattr(
            player_service,
            "get_category_config",
            lambda: {"glicko_k": 16.6, "glicko_m": 338},
        )

        rankings = player_service.load_rankings({
            "display_name": "ju",
            "page": 1,
            "page_size": 10,
        })

        assert [player["display_name"] for player in rankings] == ["Juan Samper", "Juan Soler"]

        second_page = player_service.load_rankings({
            "display_name": "ju",
            "page": 2,
            "page_size": 1,
        })
        assert [player["display_name"] for player in second_page] == ["Juan Soler"]


def test_load_player_supports_match_pagination(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            rating REAL,
            wins INTEGER,
            losses INTEGER,
            draws INTEGER,
            games_played INTEGER,
            active INTEGER,
            slug TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT,
            white_player_id INTEGER,
            black_player_id INTEGER,
            result TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY,
            player_id INTEGER,
            snapshot_date TEXT,
            rating REAL,
            rd REAL,
            volatility REAL
        )
        """
    )
    conn.executemany(
        "INSERT INTO players (id, display_name, rating, wins, losses, draws, games_played, active, slug) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "Alice", 1500, 1, 0, 0, 1, 1, "alice"),
            (2, "Bob", 1500, 1, 0, 0, 1, 1, "bob"),
        ],
    )
    conn.executemany(
        "INSERT INTO matches (id, match_date, white_player_id, black_player_id, result) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "2026-01-01", 1, 2, "1-0"),
            (2, "2026-01-02", 2, 1, "0-1"),
            (3, "2026-01-03", 1, 2, "1-0"),
        ],
    )

    monkeypatch.setattr(player_service, "get_db", lambda: conn)
    monkeypatch.setattr(
        player_service,
        "build_player_badges",
        lambda *args, **kwargs: [],
    )

    page = player_service.load_player(1, page=2, page_size=1)
    assert len(page["matches"]) == 1
    assert page["page"] == 2
    assert page["page_size"] == 1
    assert page["total_matches"] == 3
    assert page["matches"][0]["match_date"] == "2026-01-02"


def test_admin_matches_route_rejects_non_positive_page_size():
    app.testing = True
    client = app.test_client()
    conftest.set_admin_session(client)

    response = client.get("/admin/matches?page_size=0")

    assert response.status_code == 400


def test_load_player_uses_sql_level_match_pagination(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            rating REAL,
            wins INTEGER,
            losses INTEGER,
            draws INTEGER,
            games_played INTEGER,
            active INTEGER,
            slug TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT,
            white_player_id INTEGER,
            black_player_id INTEGER,
            result TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY,
            player_id INTEGER,
            snapshot_date TEXT,
            rating REAL,
            rd REAL,
            volatility REAL
        )
        """
    )
    conn.executemany(
        "INSERT INTO players (id, display_name, rating, wins, losses, draws, games_played, active, slug) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "Alice", 1500, 1, 0, 0, 1, 1, "alice"),
            (2, "Bob", 1500, 1, 0, 0, 1, 1, "bob"),
        ],
    )
    conn.executemany(
        "INSERT INTO matches (id, match_date, white_player_id, black_player_id, result) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "2026-01-01", 1, 2, "1-0"),
            (2, "2026-01-02", 2, 1, "0-1"),
            (3, "2026-01-03", 1, 2, "1-0"),
        ],
    )

    class GuardedConnection:
        def __init__(self, wrapped):
            self._wrapped = wrapped
            self.row_factory = wrapped.row_factory

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def execute(self, sql, params=()):
            sql_upper = str(sql).upper()
            if "FROM MATCHES M" in sql_upper and "LIMIT" not in sql_upper:
                raise AssertionError("Player profile match query is loading the full table instead of paginating in SQL")
            return self._wrapped.execute(sql, params)

    guarded_conn = GuardedConnection(conn)

    monkeypatch.setattr(player_service, "get_db", lambda: guarded_conn)
    monkeypatch.setattr(
        player_service,
        "build_player_badges",
        lambda *args, **kwargs: [],
    )

    page = player_service.load_player(1, page=1, page_size=2)
    assert page["total_matches"] == 3
    assert [match["match_date"] for match in page["matches"]] == ["2026-01-03", "2026-01-02"]


def test_load_player_returns_event_for_legacy_and_current_match_schemas(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            rating REAL,
            wins INTEGER,
            losses INTEGER,
            draws INTEGER,
            games_played INTEGER,
            active INTEGER,
            slug TEXT
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT,
            white_player_id INTEGER,
            black_player_id INTEGER,
            result TEXT,
            event TEXT
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY,
            player_id INTEGER,
            snapshot_date TEXT,
            rating REAL,
            rd REAL,
            volatility REAL
        );
        INSERT INTO players VALUES (1, 'Alice', 1500, 1, 0, 0, 1, 1, 'alice');
        INSERT INTO players VALUES (2, 'Bob', 1500, 0, 1, 0, 1, 1, 'bob');
        INSERT INTO matches VALUES (1, '2026-01-01', 1, 2, '1-0', 'Event 1');
        """
    )

    monkeypatch.setattr(player_service, "get_db", lambda: conn)
    monkeypatch.setattr(player_service, "build_player_badges", lambda *args, **kwargs: [])

    page = player_service.load_player(1, page=1, page_size=1)

    assert page["matches"][0]["event"] == "Event 1"


def test_load_player_uses_sql_for_period_stat_filters(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            rating REAL,
            wins INTEGER,
            losses INTEGER,
            draws INTEGER,
            games_played INTEGER,
            active INTEGER,
            slug TEXT
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT,
            white_player_id INTEGER,
            black_player_id INTEGER,
            result TEXT,
            event TEXT
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY,
            player_id INTEGER,
            snapshot_date TEXT,
            rating REAL,
            rd REAL,
            volatility REAL
        );
        INSERT INTO players VALUES (1, 'Alice', 1500, 1, 0, 0, 1, 1, 'alice');
        INSERT INTO players VALUES (2, 'Bob', 1500, 0, 1, 0, 1, 1, 'bob');
        INSERT INTO matches VALUES (1, '2026-01-01', 1, 2, '1-0', 'Event 1');
        INSERT INTO matches VALUES (2, '2026-02-01', 2, 1, '0-1', 'Event 2');
        INSERT INTO matches VALUES (3, '2026-05-01', 1, 2, '1-0', 'Event 3');
        """
    )

    class GuardedConnection:
        def __init__(self, wrapped):
            self._wrapped = wrapped
            self.seen = False

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def execute(self, sql, params=()):
            sql_upper = str(sql).upper()
            if "MATCH_DATE" in sql_upper and "BETWEEN" in sql_upper:
                self.seen = True
            return self._wrapped.execute(sql, params)

    guarded_conn = GuardedConnection(conn)
    monkeypatch.setattr(player_service, "get_db", lambda: guarded_conn)
    monkeypatch.setattr(player_service, "build_player_badges", lambda *args, **kwargs: [])

    page = player_service.load_player(1, page=1, page_size=10)

    assert page is not None
    assert guarded_conn.seen is True


def test_load_player_opponent_records_use_full_history_not_pagination(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            rating REAL,
            wins INTEGER,
            losses INTEGER,
            draws INTEGER,
            games_played INTEGER,
            active INTEGER,
            slug TEXT
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT,
            white_player_id INTEGER,
            black_player_id INTEGER,
            result TEXT,
            event TEXT
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY,
            player_id INTEGER,
            snapshot_date TEXT,
            rating REAL,
            rd REAL,
            volatility REAL
        );
        INSERT INTO players VALUES (1, 'Alice', 1500, 0, 0, 0, 0, 1, 'alice');
        INSERT INTO players VALUES (2, 'Bob', 1500, 0, 0, 0, 0, 1, 'bob');
        INSERT INTO players VALUES (3, 'Carol', 1500, 0, 0, 0, 0, 1, 'carol');
        INSERT INTO matches VALUES (1, '2026-01-01', 1, 2, '1-0', 'Event A');
        INSERT INTO matches VALUES (2, '2026-01-02', 2, 1, '0-1', 'Event B');
        INSERT INTO matches VALUES (3, '2026-01-03', 1, 2, '0-1', 'Event C');
        INSERT INTO matches VALUES (4, '2026-01-04', 2, 1, '1-0', 'Event D');
        INSERT INTO matches VALUES (5, '2026-01-05', 1, 3, '1-0', 'Event E');
        """
    )
    monkeypatch.setattr(player_service, "get_db", lambda: conn)
    monkeypatch.setattr(player_service, "build_player_badges", lambda *args, **kwargs: [])

    page = player_service.load_player(1, page=1, page_size=2)

    bob_record = next(record for record in page["opponent_records"] if record["name"] == "Bob")
    assert bob_record["wins"] == 2
    assert bob_record["losses"] == 2
    assert bob_record["draws"] == 0


def test_get_player_rank_badge_ranks_one_player_in_sql():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE players (id INTEGER PRIMARY KEY, rating REAL, games_played INTEGER)"
    )
    conn.executemany(
        "INSERT INTO players (id, rating, games_played) VALUES (?, ?, ?)",
        [(player_id, 2000 - player_id * 10, 1) for player_id in range(1, 7)],
    )

    class GuardedConnection:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def execute(self, sql, params=()):
            sql_upper = str(sql).upper()
            if "GAMES_PLAYED IS NOT NULL" in sql_upper and "ROW_NUMBER" not in sql_upper:
                raise AssertionError("rank lookup should not load every eligible player")
            return self._wrapped.execute(sql, params)

    assert player_service.get_player_rank_badge(GuardedConnection(conn), 2) == 2


def test_get_team_members_uses_targeted_lookup_for_home_page_roles():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            rating REAL,
            slug TEXT,
            wins INTEGER,
            losses INTEGER,
            draws INTEGER,
            games_played INTEGER
        )
        """
    )
    conn.executemany(
        "INSERT INTO players (id, display_name, rating, slug, wins, losses, draws, games_played) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "Cruz, Carlos", 1600, "carlos-cruz", 5, 1, 0, 6),
            (2, "Gaitan, Carlos", 1550, "carlos-gaitan", 4, 2, 0, 6),
            (3, "Rivera, Juan", 1500, "juan-rivera", 3, 2, 1, 6),
            (4, "Other Player", 1490, "other-player", 1, 2, 0, 3),
        ],
    )

    members = public_routes.get_team_members(conn)

    assert {(member["role"], member["player"]["display_name"]) for member in members} == {
        ("Presidente", "Cruz, Carlos"),
        ("Secretario", "Gaitan, Carlos"),
        ("Tesorero", "Rivera, Juan"),
    }
