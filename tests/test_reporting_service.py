import sqlite3
from datetime import date

from services.reporting_service import (
    build_date_report,
    export_report_csv,
    resolve_report_range,
)


def create_report_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            initial_rating REAL,
            rating REAL,
            country TEXT,
            club TEXT
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
            rating REAL
        );
        CREATE TABLE category_config (
            id INTEGER PRIMARY KEY,
            glicko_k REAL,
            glicko_m REAL
        );
        INSERT INTO category_config VALUES (1, 16.6, 340.0);
        INSERT INTO players VALUES
            (1, 'Alice', 1500, 1600, 'CO', 'Club A'),
            (2, 'Bob', 1500, 1500, 'US', 'Club B');
        INSERT INTO matches (id, match_date, white_player_id, black_player_id, result, event) VALUES
            (1, '2026-01-01', 1, 2, '1-0', 'League'),
            (2, '2026-01-02', 1, 2, '0-1', 'League'),
            (3, '2025-12-31', 1, 2, '1-0', 'League');
        INSERT INTO rating_snapshots (id, player_id, snapshot_date, rating) VALUES
            (1, 1, '2025-12-31', 1500),
            (2, 1, '2026-01-02', 1600),
            (3, 2, '2025-12-31', 1500),
            (4, 2, '2026-01-02', 1500);
        """
    )
    return conn


def test_resolve_report_range_uses_inclusive_calendar_boundaries():
    assert resolve_report_range("year", today=date(2026, 8, 27)) == (
        date(2026, 1, 1),
        date(2026, 12, 31),
    )
    assert resolve_report_range("custom", "2026-01-01", "2026-01-01") == (
        date(2026, 1, 1),
        date(2026, 1, 1),
    )


def test_report_uses_inclusive_range_and_precomputed_metrics():
    conn = create_report_db()
    report = build_date_report(conn, date(2026, 1, 1), date(2026, 1, 2), selected_player_id=1)

    assert report["summary"] == {
        "games": 2,
        "players": 2,
        "wins": 1,
        "losses": 1,
        "draws": 0,
        "win_percentage": 50.0,
    }
    alice = report["players"][0]
    assert alice["display_name"] == "Alice"
    assert (alice["games"], alice["wins"], alice["win_percentage"]) == (2, 1, 50.0)
    assert alice["rating_change_points"] == 100.0
    assert alice["rating_change_percentage"] == 6.7
    assert alice["category_change"] == 1
    assert report["opponents"][0]["display_name"] == "Bob"
    assert {row["display_name"] for row in report["countries"]} == {"CO", "US"}
    assert {row["display_name"] for row in report["clubs"]} == {"Club A", "Club B"}
    conn.close()


def test_player_selector_is_ordered_by_total_games():
    conn = create_report_db()
    conn.execute(
        "INSERT INTO players VALUES (?, ?, ?, ?, ?, ?)",
        (3, "Cara", 1500, 1500, "FR", "Club C"),
    )
    conn.execute(
        "INSERT INTO matches (id, match_date, white_player_id, black_player_id, result, event) VALUES (?, ?, ?, ?, ?, ?)",
        (4, "2026-01-02", 1, 3, "1/2-1/2", "League"),
    )

    report = build_date_report(conn, date(2026, 1, 1), date(2026, 1, 2))

    assert [player["player_id"] for player in report["selector_players"]] == [1, 2, 3]
    assert [player["games"] for player in report["selector_players"]] == [3, 2, 1]
    conn.close()


def test_duplicate_tournament_pairing_is_counted_once_and_export_contains_range():
    conn = create_report_db()
    conn.execute("ALTER TABLE matches ADD COLUMN tournament_pairing_id INTEGER")
    conn.execute("UPDATE matches SET tournament_pairing_id = 10 WHERE id IN (1, 2)")
    report = build_date_report(conn, "2026-01-01", "2026-01-02")

    assert report["summary"]["games"] == 1
    assert report["summary"]["players"] == 2
    assert report["excluded_games"] == 1
    exported = export_report_csv(report)
    assert "report_start_date,2026-01-01,report_end_date,2026-01-02" in exported
    assert exported.count("Alice") >= 1
    conn.close()


def test_rating_change_uses_snapshot_on_start_date():
    conn = create_report_db()
    conn.execute(
        "INSERT INTO rating_snapshots (id, player_id, snapshot_date, rating) VALUES (?, ?, ?, ?)",
        (5, 1, "2026-01-01", 1550),
    )

    report = build_date_report(conn, "2026-01-01", "2026-01-02")
    alice = next(row for row in report["players"] if row["display_name"] == "Alice")

    assert alice["rating_change_points"] == 50.0
    conn.close()
