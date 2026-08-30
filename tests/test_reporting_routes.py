import sqlite3

import config
import services.common as common


def create_route_report_db(path):
    conn = sqlite3.connect(path)
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
            tournament_pairing_id INTEGER
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
        INSERT INTO matches VALUES (1, '2026-01-01', 1, 2, '1-0', NULL);
        """
    )
    conn.commit()
    conn.close()


def test_reports_page_csv_and_pdf_preserve_selected_filters(app, monkeypatch, tmp_path):
    db_path = tmp_path / "report_routes.db"
    create_route_report_db(db_path)
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    client = app.test_client()
    query = "period=custom&start_date=2026-01-01&end_date=2026-01-01&lang=en"
    page = client.get(f"/reports?{query}")
    exported = client.get(f"/reports/export.csv?{query}")
    exported_pdf = client.get(f"/reports/export.pdf?{query}&player_id=1")

    assert page.status_code == 200
    assert "2026-01-01 - 2026-01-01" in page.get_data(as_text=True)
    assert exported.status_code == 200
    assert "report_start_date,2026-01-01,report_end_date,2026-01-01" in exported.get_data(as_text=True)
    assert exported_pdf.status_code == 200
    assert exported_pdf.content_type == "application/pdf"
    assert exported_pdf.data.startswith(b"%PDF-")
    assert "report_Alice_2026-01-01_to_2026-01-01.pdf" in exported_pdf.headers["Content-Disposition"]

    body = page.get_data(as_text=True)
    assert 'id="report-season"' in body
    assert 'onchange="this.form.submit()"' in body
    assert 'id="report-period"' not in body
    assert "apply_filters" not in body
    assert 'href="/player/view?id=1&amp;lang=en"' in body

    selected_page = client.get(f"/reports?{query}&player_id=1")
    selected_body = selected_page.get_data(as_text=True)
    assert 'option value="1" selected' in selected_body
    assert "Results vs Opponents" in selected_body


def test_reports_default_to_all_time_for_general_and_player_views(app, monkeypatch, tmp_path):
    db_path = tmp_path / "report_default_routes.db"
    create_route_report_db(db_path)
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    client = app.test_client()
    general_body = client.get("/reports?lang=en").get_data(as_text=True)
    player_body = client.get("/reports?lang=en&player_id=1").get_data(as_text=True)
    default_pdf = client.get("/reports/export.pdf?lang=en")

    assert 'option value="" selected' in general_body
    assert 'option value="" selected' in player_body
    assert "All seasons" in general_body
    assert "All seasons" in player_body
    assert "apply_filters" not in general_body
    assert "report_all-players_All-time.pdf" in default_pdf.headers["Content-Disposition"]
    assert "2026-01-01 - 2026-01-01" not in general_body
    assert "2026-01-01 - 2026-01-01" not in player_body


def test_reports_paginate_player_performance_with_default_page_size(app, monkeypatch, tmp_path):
    db_path = tmp_path / "report_pagination.db"
    create_route_report_db(db_path)
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO players (id, display_name, initial_rating, rating, country, club) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (player_id, f"Player {player_id:02d}", 1500, 1500, "CO", "Club A")
            for player_id in range(3, 28)
        ],
    )
    conn.executemany(
        "INSERT INTO matches (id, match_date, white_player_id, black_player_id, result, tournament_pairing_id) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (match_id, "2026-01-01", 1, match_id + 1, "1-0", None)
            for match_id in range(2, 27)
        ],
    )
    conn.commit()
    conn.close()

    client = app.test_client()
    first_page = client.get("/reports?lang=en")
    second_page = client.get("/reports?lang=en&page=2")

    assert first_page.status_code == 200
    assert second_page.status_code == 200
    first_body = first_page.get_data(as_text=True)
    second_body = second_page.get_data(as_text=True)
    assert 'id="report-page-size"' in first_body
    assert "Page 1 of 2" in first_body
    assert ">Player 27</a>" not in first_body
    assert ">Player 27</a>" in second_body
    assert "Page 2 of 2" in second_body


def test_reports_reject_reversed_custom_range(app, monkeypatch, tmp_path):
    db_path = tmp_path / "invalid_report_routes.db"
    create_route_report_db(db_path)
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(common, "DB_PATH", str(db_path))

    response = app.test_client().get(
        "/reports?period=custom&start_date=2026-01-02&end_date=2026-01-01"
    )

    assert response.status_code == 400