import sqlite3
from datetime import date

from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4

import services.reporting_service as reporting_service
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
        "players": 1,
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
    assert [row["display_name"] for row in report["players"]] == ["Alice"]
    assert len(report["selector_players"]) == 2
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


def test_player_report_is_ordered_by_total_games():
    conn = create_report_db()
    conn.execute(
        "INSERT INTO players VALUES (?, ?, ?, ?, ?, ?)",
        (3, "Cara", 1500, 1500, "FR", "Club C"),
    )
    conn.executemany(
        "INSERT INTO matches (id, match_date, white_player_id, black_player_id, result, event) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (4, "2026-01-03", 1, 3, "0-1", "League"),
            (5, "2026-01-04", 1, 3, "0-1", "League"),
        ],
    )

    report = build_date_report(conn, date(2026, 1, 1), date(2026, 1, 4))

    assert [player["display_name"] for player in report["players"]] == ["Alice", "Cara", "Bob"]
    assert [player["games"] for player in report["players"]] == [4, 2, 2]
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


def test_player_rating_chart_respects_selected_report_period():
    conn = create_report_db()
    conn.execute(
        "INSERT INTO rating_snapshots (id, player_id, snapshot_date, rating) VALUES (?, ?, ?, ?)",
        (5, 1, "2026-01-01", 1550),
    )

    report = build_date_report(conn, "2026-01-01", "2026-01-02", selected_player_id=1)

    assert [point["date"] for point in report["rating_chart"]["points"]] == [
        "2026-01-01",
        "2026-01-02",
    ]
    conn.close()


def test_pdf_player_table_includes_rating_changes_in_portrait_layout(monkeypatch):
    captured = {"tables": []}

    class FakeDocument:
        def __init__(self, _output, **kwargs):
            captured["pagesize"] = kwargs["pagesize"]

        def build(self, _story):
            captured["story"] = _story
            return None

    def capture_table(rows, col_widths=None, separator_columns=(), cell_padding=5, header_font_size=8):
        captured["tables"].append((rows, col_widths))
        captured["separators"] = separator_columns
        return object()

    monkeypatch.setattr(reporting_service, "SimpleDocTemplate", FakeDocument)
    monkeypatch.setattr(reporting_service, "_pdf_table", capture_table)

    report = {
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
        "summary": {"games": 1, "players": 1},
        "players": [
            {
                "rank": 1,
                "display_name": "Alice",
                "games": 1,
                "wins": 1,
                "losses": 0,
                "draws": 0,
                "win_percentage": 100.0,
                "rating_change_points": 100.0,
                "rating_change_percentage": 6.7,
            }
        ],
        "selected_player_id": 1,
        "opponents": [],
        "rating_chart": {
            "points": [{"x": 24, "y": 100, "date": "2026-01-01", "rating": 1500}],
            "min_rating": 1480,
            "max_rating": 1520,
        },
    }

    reporting_service.export_report_pdf(report, {"reports_title": "Reports"})

    assert captured["pagesize"] == A4
    player_rows, player_widths = captured["tables"][1]
    assert player_rows[0][-2:] == ["Rating points", "Rating %"]
    assert player_rows[1][-2:] == ["+100.00", "+6.7%"]
    assert player_widths is not None
    assert any(isinstance(item, Drawing) for item in captured["story"])


def test_pdf_rating_chart_uses_categories_for_axis_labels(monkeypatch):
    monkeypatch.setattr(
        reporting_service,
        "glicko_to_category",
        lambda rating: f"category-{rating:g}",
    )

    drawing = reporting_service._pdf_rating_chart(
        {
            "points": [{"x": 24, "y": 100, "date": "2026-01-01", "rating": 1500}],
            "min_rating": 1000,
            "max_rating": 2000,
        }
    )

    axis_labels = [
        item.text
        for item in drawing.contents
        if isinstance(item, reporting_service.String)
    ]
    assert "category-1000" in axis_labels
    assert "category-1500" in axis_labels
    assert "category-2000" in axis_labels


def test_pdf_opponent_table_is_two_up_without_draws_column(monkeypatch):
    captured = {}

    def capture_table(rows, col_widths=None, separator_columns=(), cell_padding=5, header_font_size=8):
        captured["rows"] = rows
        captured["col_widths"] = col_widths
        captured["separators"] = separator_columns
        captured["cell_padding"] = cell_padding
        captured["header_font_size"] = header_font_size
        return object()

    monkeypatch.setattr(reporting_service, "_pdf_table", capture_table)
    labels = {
        "opponent": "Opponent",
        "games": "Games",
        "wins": "Wins",
        "losses": "Losses",
        "win_percentage": "Win percentage",
    }
    opponents = [
        {"display_name": "Alice", "games": 3, "wins": 2, "losses": 1, "win_percentage": 66.7},
        {"display_name": "Bob", "games": 2, "wins": 1, "losses": 1, "win_percentage": 50.0},
        {"display_name": "Carol", "games": 1, "wins": 0, "losses": 1, "win_percentage": 0.0},
    ]

    reporting_service._pdf_opponent_table(opponents, labels)

    assert captured["rows"][0] == [
        "Opponent", "Games", "Wins", "Losses", "%", "",
        "Opponent", "Games", "Wins", "Losses", "%",
    ]
    assert all(len(row) == 11 for row in captured["rows"])
    assert captured["rows"][-1][-5:] == ["", "", "", "", ""]
    assert len(captured["col_widths"]) == 11
    assert abs(sum(captured["col_widths"]) / reporting_service.mm - 194) < 1e-9
    assert captured["col_widths"][5] < captured["col_widths"][1]
    assert captured["cell_padding"] == 3
    assert captured["header_font_size"] == 7
    assert captured["separators"] == (5,)
    assert "Draws" not in captured["rows"][0]
