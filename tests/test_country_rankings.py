import re
import sqlite3

import pytest

import conftest
import services.player_service as player_service
from app import app, ensure_player_schema_columns
from services.country_service import (
    country_name,
    migrate_player_country_codes,
    normalize_country_code,
)
from services.display_name import create_display_name_views
from services.i18n import TRANSLATIONS
from services.settings_service import ensure_application_settings_table
from services.timezone_service import current_date


def test_country_codes_normalize_and_localize():
    assert normalize_country_code("co") == "COL"
    assert normalize_country_code("Spain") == "ESP"
    assert country_name("COL", "es") == "Colombia"
    assert country_name("BR", "pt") == "Brasil"
    with pytest.raises(ValueError):
        normalize_country_code("Atlantis")


def test_country_migration_converts_known_values_and_preserves_unknown():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE players (id INTEGER PRIMARY KEY, country TEXT)"
    )
    conn.executemany(
        "INSERT INTO players (id, country) VALUES (?, ?)",
        [(1, "CO"), (2, "USA"), (3, "legacy-place")],
    )

    migrate_player_country_codes(conn)

    values = conn.execute(
        "SELECT country FROM players ORDER BY id"
    ).fetchall()
    assert values == [("COL",), ("USA",), ("legacy-place",)]
    conn.close()


def test_player_schema_migration_adds_demographics_and_normalizes_countries():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE players (id INTEGER PRIMARY KEY, country TEXT)"
    )
    conn.executemany(
        "INSERT INTO players (id, country) VALUES (?, ?)",
        [(1, "CO"), (2, "legacy-place")],
    )

    ensure_player_schema_columns(conn)

    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(players)").fetchall()
    }
    values = conn.execute(
        "SELECT country FROM players ORDER BY id"
    ).fetchall()
    assert {"birth_year", "gender"}.issubset(columns)
    assert values == [("COL",), ("legacy-place",)]
    conn.close()


def _make_rankings_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            custom_display_name TEXT,
            country TEXT,
            birth_year INTEGER,
            gender TEXT,
            club TEXT,
            slug TEXT,
            active INTEGER DEFAULT 1,
            rating REAL,
            rd REAL DEFAULT 50,
            volatility REAL DEFAULT 0.06,
            games_played INTEGER,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            draws INTEGER DEFAULT 0
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT,
            white_player_id INTEGER,
            black_player_id INTEGER,
            result TEXT
        );
        """
    )
    ensure_application_settings_table(conn)
    create_display_name_views(conn)
    year = current_date().year
    conn.executemany(
        """
        INSERT INTO players
            (id, first_name, country, birth_year, gender, slug, rating, games_played)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "Alice", "COL", year - 18, "woman", "alice", 1900, 10),
            (2, "Bob", "COL", year - 17, None, "bob", 1800, 8),
            (3, "Cara", "ARG", year - 20, "woman", "cara", 1700, 7),
            (4, "Dana", "USA", year - 18, "woman", "dana", 1600, 6),
        ],
    )
    conn.commit()
    conn.close()


def test_rankings_filter_by_country_region_and_demographic_group(monkeypatch, tmp_path):
    db_path = tmp_path / "country-rankings.db"
    _make_rankings_db(db_path)

    def connect_db():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(player_service, "get_db", connect_db)
    monkeypatch.setattr(
        player_service,
        "get_category_config",
        lambda: {"glicko_k": 1, "glicko_m": 1500},
    )

    def ids(filters):
        return [
            row["id"]
            for row in player_service.load_rankings(
                {"games_played_min": 1, **filters}
            )
        ]

    assert ids({"country": "CO"}) == [1, 2]
    assert player_service.count_rankings(
        {"games_played_min": 1, "country": "COL"}
    ) == 2
    assert ids({"region": "latin-america-caribbean"}) == [1, 2, 3]
    assert ids({"group": "u18"}) == [1, 2, 4]
    assert ids({"group": "women"}) == [1, 3, 4]
    assert ids({
        "country": "COL",
        "region": "latin-america-caribbean",
        "group": "women",
    }) == [1]
    assert ids({"group": ["u18", "women"]}) == [1, 4]
    with pytest.raises(ValueError):
        player_service.count_rankings({"group": "unsupported"})


def test_profile_awards_country_region_u18_and_women_rank_badges():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            country TEXT,
            birth_year INTEGER,
            gender TEXT,
            rating REAL,
            games_played INTEGER
        )
        """
    )
    year = current_date().year
    conn.executemany(
        "INSERT INTO players VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, "COL", year - 18, "woman", 1900, 10),
            (2, "COL", year - 18, "woman", 1800, 9),
            (3, "ARG", year - 19, "woman", 1700, 8),
        ],
    )
    player = conn.execute("SELECT * FROM players WHERE id = 2").fetchone()

    badges = player_service.get_player_scope_rank_badges(
        conn, player, TRANSLATIONS["en"], lang="en"
    )

    assert {
        (badge["scope_label"], badge["label"])
        for badge in badges
    } == {
        ("Colombia", "#2"),
        ("Latin America and the Caribbean", "#2"),
        ("Sub-18", "#2"),
        ("Female", "#2"),
    }
    conn.close()


def test_admin_player_form_saves_canonical_country_and_public_demographics(
        isolate_test_database, app):
    db_path = isolate_test_database
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO players
            (id, first_name, last_name, slug, country, active, rating, initial_rating)
        VALUES (9101, 'Test', 'Player', 'country-test-player', 'CO', 1, 1500, 1500)
        """
    )
    conn.commit()
    conn.close()

    client = app.test_client()
    conftest.set_admin_session(client, db_path)
    page = client.get("/admin/players/edit?id=9101&lang=en")
    assert page.status_code == 200
    assert b'name="birth_year"' in page.data
    assert b'name="gender" value="woman"' in page.data
    assert b'type="checkbox"' in page.data
    assert b"Gender identity" not in page.data
    year = current_date().year

    response = client.post(
        "/admin/players/edit?id=9101&lang=en",
        data={
            "first_name": "Test",
            "last_name": "Player",
            "slug": "country-test-player",
            "initial_rating": "1500",
            "active": "1",
            "country": "CO",
            "birth_year": str(year - 18),
            "gender": "woman",
            "club": "",
        },
    )
    assert response.status_code == 302

    conn = sqlite3.connect(db_path)
    saved = conn.execute(
        "SELECT country, birth_year, gender FROM players WHERE id = 9101"
    ).fetchone()
    conn.close()
    assert saved == ("COL", year - 18, "woman")

    profile = client.get("/player/view?id=9101&lang=en")
    assert profile.status_code == 200
    assert b"Colombia" in profile.data
    assert str(year - 18).encode() in profile.data
    assert b"Female" in profile.data
    assert b"Gender identity" not in profile.data

    edited_player = client.get("/admin/players/edit?id=9101&lang=en")
    assert re.search(rb'name="gender" value="woman"\s+checked', edited_player.data)

    response = client.post(
        "/admin/players/edit?id=9101&lang=en",
        data={
            "first_name": "Test",
            "last_name": "Player",
            "slug": "country-test-player",
            "initial_rating": "1500",
            "active": "1",
            "country": "CO",
            "birth_year": str(year - 18),
            "club": "",
        },
    )
    assert response.status_code == 302
    conn = sqlite3.connect(db_path)
    saved_gender = conn.execute(
        "SELECT gender FROM players WHERE id = 9101"
    ).fetchone()[0]
    conn.close()
    assert saved_gender is None


def test_public_rankings_render_scopes_and_reject_invalid_values(app):
    client = app.test_client()
    response = client.get(
        "/rankings?lang=en&country=CO&region=latin-america-caribbean&group=u18&group=women"
    )
    assert response.status_code == 200
    assert b"Latin America and the Caribbean" in response.data
    assert b'name="country"' in response.data
    assert b'name="group" value="u18"' in response.data
    assert b'name="group" value="women"' in response.data
    assert re.search(rb'name="group" value="u18"\s+checked', response.data)
    assert re.search(rb'name="group" value="women"\s+checked', response.data)
    assert client.get("/rankings?lang=en&region=unknown").status_code == 400


def test_public_rankings_country_filter_only_lists_countries_in_database(
        app, isolate_test_database):
    conn = sqlite3.connect(isolate_test_database)
    conn.execute(
        """
        INSERT INTO players
            (first_name, last_name, slug, country, active, rating, games_played)
        VALUES ('Country', 'Filter', 'country-filter-test', 'COL', 1, 1500, 0)
        """
    )
    conn.commit()
    conn.close()

    response = app.test_client().get("/rankings?lang=en")

    assert response.status_code == 200
    assert re.search(rb'<option value="COL"[^>]*>Colombia</option>', response.data)
    assert not re.search(rb'<option value="USA"[^>]*>United States</option>', response.data)
