import sqlite3
from datetime import date

import services.home_stats as home_stats
import services.player_service as player_service
from services.common import TRANSLATIONS
from services.home_stats import build_home_stats, build_player_badges
from services.player_service import get_player_rank_badge, load_player


def test_build_home_stats_returns_period_leaders(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            initial_rating REAL,
            rating REAL
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT,
            white_player_id INTEGER,
            black_player_id INTEGER,
            result TEXT
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY,
            player_id INTEGER,
            snapshot_date TEXT,
            rating REAL
        );
        """
    )

    conn.executemany(
        "INSERT INTO players (id, display_name, initial_rating, rating) VALUES (?, ?, ?, ?)",
        [
            (1, "Alice", 1500.0, 1700.0),
            (2, "Bob", 1500.0, 1600.0),
        ],
    )

    conn.executemany(
        "INSERT INTO matches (id, match_date, white_player_id, black_player_id, result) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "2024-01-01", 1, 2, "1-0"),
            (2, "2024-02-01", 1, 2, "0-1"),
            (3, "2025-01-01", 1, 2, "1-0"),
            (4, "2025-02-01", 2, 1, "0-1"),
        ],
    )

    conn.executemany(
        "INSERT INTO rating_snapshots (id, player_id, snapshot_date, rating) VALUES (?, ?, ?, ?)",
        [
            (1, 1, "2024-01-01", 1500.0),
            (2, 1, "2024-02-01", 1600.0),
            (3, 1, "2025-01-01", 1650.0),
            (4, 2, "2024-01-01", 1500.0),
            (5, 2, "2025-02-01", 1600.0),
        ],
    )

    def fixed_period_bounds(period):
        if period == "year":
            return date(2025, 1, 1), date(2025, 12, 31)
        if period == "quarter":
            return date(2025, 1, 1), date(2025, 3, 31)
        return None, None

    monkeypatch.setattr(home_stats, "_period_bounds", fixed_period_bounds)

    stats = build_home_stats(conn=conn)

    assert stats["all_time"]["most_active"][0]["display_name"] == "Alice"
    assert stats["all_time"]["most_wins"][0]["display_name"] == "Alice"
    assert stats["year"]["most_wins_as_white"][0]["display_name"] == "Alice"
    assert stats["quarter"]["most_wins_as_black"][0]["display_name"] == "Alice"
    assert stats["all_time"]["relative_glicko_gain"][0]["display_name"] == "Alice"

    conn.close()


def test_year_stats_include_matches_with_non_iso_date_strings(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            initial_rating REAL,
            rating REAL
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT,
            white_player_id INTEGER,
            black_player_id INTEGER,
            result TEXT
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY,
            player_id INTEGER,
            snapshot_date TEXT,
            rating REAL
        );
        """
    )

    conn.executemany(
        "INSERT INTO players (id, display_name, initial_rating, rating) VALUES (?, ?, ?, ?)",
        [
            (1, "Alice", 1500.0, 1500.0),
            (2, "Bob", 1500.0, 1500.0),
        ],
    )

    conn.execute(
        "INSERT INTO matches (id, match_date, white_player_id, black_player_id, result) VALUES (?, ?, ?, ?, ?)",
        (1, "2025/01/01", 1, 2, "1-0"),
    )

    monkeypatch.setattr(home_stats, "_period_bounds", lambda period: (date(2025, 1, 1), date(2025, 12, 31)) if period == "year" else (None, None))

    stats = build_home_stats(conn=conn)

    assert stats["year"]["most_wins"][0]["player_id"] == 1
    assert stats["year"]["most_wins"][0]["value"] == 1

    conn.close()


def test_build_metric_entries_allows_zero_when_requested():
    players = [
        {"player_id": 1, "display_name": "Alice"},
        {"player_id": 2, "display_name": "Bob"},
    ]
    metric_values = {1: 0, 2: 2}

    entries = home_stats._build_metric_entries(players, metric_values, min_value=0)

    assert [entry["player_id"] for entry in entries] == [2, 1]
    assert entries[-1]["value"] == 0


def test_home_stats_filters_periods_in_sql_not_python(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            initial_rating REAL,
            rating REAL
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT,
            white_player_id INTEGER,
            black_player_id INTEGER,
            result TEXT
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY,
            player_id INTEGER,
            snapshot_date TEXT,
            rating REAL
        );
        """
    )

    conn.executemany(
        "INSERT INTO matches (id, match_date, white_player_id, black_player_id, result) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "2024-01-01", 1, 2, "1-0"),
            (2, "2025-01-01", 1, 2, "0-1"),
            (3, "2026-01-01", 1, 2, "1-0"),
        ],
    )
    conn.executemany(
        "INSERT INTO rating_snapshots (id, player_id, snapshot_date, rating) VALUES (?, ?, ?, ?)",
        [
            (1, 1, "2024-01-01", 1500.0),
            (2, 1, "2025-01-01", 1600.0),
            (3, 1, "2026-01-01", 1700.0),
        ],
    )

    class GuardedConnection:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def execute(self, sql, params=()):
            sql_upper = str(sql).upper()
            if "FROM MATCHES" in sql_upper and "MATCH_DATE" not in sql_upper:
                raise AssertionError("Period match filtering should happen in SQL")
            if "FROM RATING_SNAPSHOTS" in sql_upper and "SNAPSHOT_DATE" not in sql_upper:
                raise AssertionError("Snapshot period filtering should happen in SQL")
            return self._wrapped.execute(sql, params)

    guarded = GuardedConnection(conn)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 12, 31)

    rows = home_stats._matches_in_period(guarded, start_date, end_date)
    snapshots = home_stats._snapshots_in_period(guarded, 1, start_date, end_date)

    assert [row["id"] for row in rows] == [2]
    assert [row["snapshot_date"] for row in snapshots] == ["2025-01-01"]

    conn.close()


def test_period_stats_counts_matches_in_one_pass(monkeypatch):
    class GuardedMatches(list):
        def __iter__(self):
            if getattr(self, "_iterated", False):
                raise AssertionError("period stats should not re-iterate all match rows for each player")
            self._iterated = True
            return super().__iter__()

    player_rows = [
        {"player_id": 1, "display_name": "Alice", "initial_rating": 1500.0, "rating": 1500.0},
        {"player_id": 2, "display_name": "Bob", "initial_rating": 1500.0, "rating": 1500.0},
    ]
    matches = GuardedMatches([
        {"white_player_id": 1, "black_player_id": 2, "result": "1-0"},
        {"white_player_id": 2, "black_player_id": 1, "result": "0-1"},
        {"white_player_id": 1, "black_player_id": 2, "result": "1/2-1/2"},
    ])

    class DummyConn:
        def execute(self, sql, params=()):
            sql_text = str(sql).upper()
            if "FROM PLAYERS" in sql_text:
                return type("Rows", (), {"fetchall": lambda self: player_rows})()
            return type("Rows", (), {"fetchall": lambda self: []})()

    monkeypatch.setattr(home_stats, "_matches_in_period", lambda conn, start_date, end_date: matches)
    monkeypatch.setattr(home_stats, "_snapshots_in_period", lambda conn, player_id, start_date, end_date: [])

    result = home_stats._period_stats(DummyConn(), "year")
    assert result["most_active"][0]["player_id"] == 1
    assert result["most_wins"][0]["player_id"] == 1


def test_build_player_badges_returns_ranked_achievements():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            initial_rating REAL,
            rating REAL
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT,
            white_player_id INTEGER,
            black_player_id INTEGER,
            result TEXT
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY,
            player_id INTEGER,
            snapshot_date TEXT,
            rating REAL
        );
        """
    )

    conn.executemany(
        "INSERT INTO players (id, display_name, initial_rating, rating) VALUES (?, ?, ?, ?)",
        [
            (1, "Alice", 1500.0, 1700.0),
            (2, "Bob", 1500.0, 1600.0),
            (3, "Cara", 1500.0, 1550.0),
        ],
    )

    conn.executemany(
        "INSERT INTO matches (id, match_date, white_player_id, black_player_id, result) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "2024-01-01", 1, 2, "1-0"),
            (2, "2024-02-01", 1, 3, "1-0"),
            (3, "2025-02-01", 2, 1, "0-1"),
        ],
    )

    conn.executemany(
        "INSERT INTO rating_snapshots (id, player_id, snapshot_date, rating) VALUES (?, ?, ?, ?)",
        [
            (1, 1, "2024-01-01", 1500.0),
            (2, 1, "2024-02-01", 1600.0),
            (3, 1, "2025-02-01", 1700.0),
            (4, 2, "2024-01-01", 1500.0),
            (5, 2, "2025-02-01", 1600.0),
        ],
    )

    badges = build_player_badges(1, conn=conn)

    assert any(
        badge["label"] == TRANSLATIONS["en"]["stats_metric_active"]
        for badge in badges
    )
    assert any(
        badge["label"] == TRANSLATIONS["en"]["stats_metric_wins"]
        for badge in badges
    )

    conn.close()


def test_build_player_badges_skips_zero_game_and_zero_win_players():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            initial_rating REAL,
            rating REAL,
            games_played INTEGER DEFAULT 0
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT,
            white_player_id INTEGER,
            black_player_id INTEGER,
            result TEXT
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY,
            player_id INTEGER,
            snapshot_date TEXT,
            rating REAL
        );
        """
    )

    conn.executemany(
        "INSERT INTO players (id, display_name, initial_rating, rating, games_played) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "Alice", 1500.0, 1500.0, 1),
            (2, "Bob", 1500.0, 1500.0, 0),
            (3, "Cara", 1500.0, 1500.0, 0),
        ],
    )

    conn.execute(
        "INSERT INTO matches (id, match_date, white_player_id, black_player_id, result) VALUES (?, ?, ?, ?, ?)",
        (1, "2024-01-01", 2, 1, "0-1"),
    )

    zero_game_badges = build_player_badges(3, conn=conn)
    assert not any(
        badge["label"] == TRANSLATIONS["en"]["stats_metric_active"]
        for badge in zero_game_badges
    )
    assert zero_game_badges == []

    zero_win_badges = build_player_badges(2, conn=conn)
    assert not any(
        badge["label"] == TRANSLATIONS["en"]["stats_metric_wins"]
        for badge in zero_win_badges
    )

    conn.close()


def test_build_player_badges_returns_empty_for_zero_game_player_even_if_top_rated():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            initial_rating REAL,
            rating REAL,
            games_played INTEGER DEFAULT 0
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT,
            white_player_id INTEGER,
            black_player_id INTEGER,
            result TEXT
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY,
            player_id INTEGER,
            snapshot_date TEXT,
            rating REAL
        );
        """
    )

    conn.executemany(
        "INSERT INTO players (id, display_name, initial_rating, rating, games_played) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "Alice", 1500.0, 1700.0, 0),
            (2, "Bob", 1500.0, 1600.0, 2),
        ],
    )

    badges = build_player_badges(1, conn=conn)
    assert badges == []

    conn.close()


def test_build_player_badges_only_marks_the_top_rated_player():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            initial_rating REAL,
            rating REAL
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT,
            white_player_id INTEGER,
            black_player_id INTEGER,
            result TEXT
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY,
            player_id INTEGER,
            snapshot_date TEXT,
            rating REAL
        );
        """
    )

    conn.executemany(
        "INSERT INTO players (id, display_name, initial_rating, rating) VALUES (?, ?, ?, ?)",
        [
            (1, "Alice", 1500.0, 1700.0),
            (2, "Bob", 1500.0, 1600.0),
        ],
    )

    top_player_badges = build_player_badges(1, conn=conn)
    assert any(
        badge["label"] == TRANSLATIONS["en"]["stats_badge_top_rated"]
        for badge in top_player_badges
    )

    non_top_player_badges = build_player_badges(2, conn=conn)
    assert not any(
        badge["label"] == TRANSLATIONS["en"]["stats_badge_top_rated"]
        for badge in non_top_player_badges
    )

    conn.close()


def test_build_player_badges_only_awards_the_single_top_metric_winner():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            initial_rating REAL,
            rating REAL
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT,
            white_player_id INTEGER,
            black_player_id INTEGER,
            result TEXT
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY,
            player_id INTEGER,
            snapshot_date TEXT,
            rating REAL
        );
        """
    )

    conn.executemany(
        "INSERT INTO players (id, display_name, initial_rating, rating) VALUES (?, ?, ?, ?)",
        [
            (1, "Alice", 1500.0, 1600.0),
            (2, "Bob", 1500.0, 1550.0),
            (3, "Cara", 1500.0, 1500.0),
        ],
    )

    conn.executemany(
        "INSERT INTO matches (id, match_date, white_player_id, black_player_id, result) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "2025-01-01", 1, 2, "1-0"),
            (2, "2025-01-02", 1, 3, "1-0"),
            (3, "2025-01-03", 2, 3, "1-0"),
            (4, "2025-01-04", 3, 1, "0-1"),
        ],
    )

    badges = build_player_badges(2, conn=conn)
    assert not any(
        badge["label"] == TRANSLATIONS["en"]["stats_metric_wins"]
        for badge in badges
    )

    conn.close()


def test_build_player_badges_awards_translated_yearly_leaders():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE players (id INTEGER PRIMARY KEY, display_name TEXT, initial_rating REAL, rating REAL);
        CREATE TABLE matches (id INTEGER PRIMARY KEY, match_date TEXT, white_player_id INTEGER, black_player_id INTEGER, result TEXT);
        CREATE TABLE rating_snapshots (id INTEGER PRIMARY KEY, player_id INTEGER, snapshot_date TEXT, rating REAL);
        """
    )
    conn.executemany(
        "INSERT INTO players VALUES (?, ?, ?, ?)",
        [(1, "Alice", 1500, 1800), (2, "Bob", 1500, 1800), (3, "Cara", 1500, 1500)],
    )
    conn.executemany(
        "INSERT INTO matches VALUES (?, ?, ?, ?, ?)",
        [
            (1, "2024-01-01", 1, 2, "1-0"),
            (2, "2024-02-01", 1, 2, "0-1"),
            (3, "2025-01-01", 2, 1, "0-1"),
            (4, "2025-02-01", 2, 1, "0-1"),
            (5, "2025-03-01", 2, 1, "1-0"),
            (6, "2025-04-01", 2, 1, "1-0"),
            (7, "2025-05-01", 2, 1, "1-0"),
            (8, "2025-06-01", 2, 1, "1-0"),
            (9, "2025-07-01", 2, 3, "1-0"),
        ],
    )
    conn.executemany(
        "INSERT INTO rating_snapshots VALUES (?, ?, ?, ?)",
        [
            (1, 1, "2024-01-01", 1500), (2, 1, "2024-12-01", 1600),
            (3, 2, "2024-01-01", 1500), (4, 2, "2024-12-01", 1550),
            (5, 1, "2025-01-01", 1600), (6, 1, "2025-12-01", 1650),
            (7, 2, "2025-01-01", 1550), (8, 2, "2025-12-01", 1800),
        ],
    )

    badges = build_player_badges(1, translations=TRANSLATIONS["es"], conn=conn)

    yearly_badges = {(badge["label"], badge["period"]) for badge in badges if str(badge["period"]).isdigit()}
    assert (TRANSLATIONS["es"]["stats_metric_active"], "2024") in yearly_badges
    assert (TRANSLATIONS["es"]["stats_metric_wins"], "2024") in yearly_badges
    assert (TRANSLATIONS["es"]["stats_metric_glicko"], "2024") in yearly_badges
    assert (TRANSLATIONS["es"]["stats_metric_active"], "2025") not in yearly_badges
    assert (TRANSLATIONS["es"]["stats_metric_glicko"], "2025") not in yearly_badges
    conn.close()


def test_get_player_rank_badge_returns_rank_for_top_five_with_games():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            initial_rating REAL,
            rating REAL,
            games_played INTEGER
        );
        """
    )

    conn.executemany(
        "INSERT INTO players (id, display_name, initial_rating, rating, games_played) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "Alice", 1500.0, 1800.0, 2),
            (2, "Bob", 1500.0, 1700.0, 1),
            (3, "Cara", 1500.0, 1600.0, 1),
            (4, "Dan", 1500.0, 1500.0, 0),
        ],
    )

    assert get_player_rank_badge(conn, 1) == 1
    assert get_player_rank_badge(conn, 2) == 2
    assert get_player_rank_badge(conn, 3) == 3
    assert get_player_rank_badge(conn, 4) is None

    conn.close()


def test_load_player_computes_rank_badge_before_connection_closes(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            slug TEXT,
            initial_rating REAL,
            rating REAL,
            games_played INTEGER,
            wins INTEGER,
            losses INTEGER,
            draws INTEGER,
            country TEXT,
            club TEXT,
            rd REAL,
            volatility REAL
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT,
            result TEXT,
            event TEXT,
            white_player_id INTEGER,
            black_player_id INTEGER
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY,
            player_id INTEGER,
            snapshot_date TEXT,
            rating REAL,
            rd REAL,
            volatility REAL
        );
        """
    )

    conn.execute(
        "INSERT INTO players (id, display_name, slug, initial_rating, rating, games_played, wins, losses, draws, country, club, rd, volatility) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (1, "Alice", "alice", 1500.0, 1800.0, 2, 2, 0, 0, "COL", "", 50.0, 0.06),
    )
    conn.execute(
        "INSERT INTO players (id, display_name, slug, initial_rating, rating, games_played, wins, losses, draws, country, club, rd, volatility) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (2, "Bob", "bob", 1500.0, 1700.0, 1, 1, 0, 0, "COL", "", 50.0, 0.06),
    )
    conn.execute(
        "INSERT INTO matches (id, match_date, result, event, white_player_id, black_player_id) VALUES (?, ?, ?, ?, ?, ?)",
        (1, "2024-01-01", "1-0", "", 1, 2),
    )

    monkeypatch.setattr(player_service, "get_db", lambda: conn)

    data = load_player(1)

    assert data is not None
    assert any(badge["label"] == "#1" for badge in data["badges"])

    conn.close()
