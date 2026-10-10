import sqlite3

import pytest

from services.display_name import (
    create_display_name_views,
    ensure_name_comma_guards,
    migrate_display_name_schema,
    render_display_name,
    split_display_name,
)
from services.settings_service import (
    DEFAULT_APPLICATION_SETTINGS,
    ensure_application_settings_table,
    update_application_settings,
    validate_application_settings,
)


BASE_SETTINGS = {
    "max_login_attempts": 5,
    "login_window_seconds": 900,
    "password_reset_ttl_seconds": 3600,
}


def create_players_schema(connection):
    connection.execute(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            custom_display_name TEXT,
            country TEXT,
            club TEXT,
            slug TEXT UNIQUE,
            initial_rating REAL,
            rating REAL,
            rd REAL,
            volatility REAL,
            active INTEGER DEFAULT 1
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE tournament_pending_players (
            id INTEGER PRIMARY KEY,
            tournament_id INTEGER NOT NULL DEFAULT 1,
            first_name TEXT NOT NULL DEFAULT '',
            last_name TEXT NOT NULL DEFAULT '',
            suggested_name TEXT,
            rating REAL,
            rank INTEGER,
            category TEXT,
            source_key TEXT,
            participating INTEGER DEFAULT 1,
            created_at TEXT
        )
        """
    )


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "display.db")
    connection.row_factory = sqlite3.Row
    create_players_schema(connection)
    ensure_application_settings_table(connection)
    create_display_name_views(connection)
    connection.commit()
    yield connection
    connection.close()


def set_format(conn, fmt, template=""):
    return update_application_settings(
        {**BASE_SETTINGS, "display_name_format": fmt, "display_name_template": template},
        conn=conn,
    )


def add_player(conn, player_id, first, last, custom=None):
    conn.execute(
        "INSERT INTO players (id, first_name, last_name, custom_display_name, slug, rating, active) "
        "VALUES (?, ?, ?, ?, ?, 1500, 1)",
        (player_id, first, last, custom, f"player-{player_id}"),
    )
    conn.commit()


def rendered_names(conn):
    return dict(conn.execute("SELECT id, display_name FROM players_display ORDER BY id").fetchall())


def test_validate_rejects_unknown_format_and_bad_custom_templates():
    with pytest.raises(ValueError, match="display_name_format"):
        validate_application_settings(
            {**BASE_SETTINGS, "display_name_format": "nope", "display_name_template": ""})
    with pytest.raises(ValueError, match="display_name_template"):
        validate_application_settings(
            {**BASE_SETTINGS, "display_name_format": "custom", "display_name_template": "no placeholders"})
    with pytest.raises(ValueError, match="display_name_template"):
        validate_application_settings(
            {**BASE_SETTINGS, "display_name_format": "custom",
             "display_name_template": "{first_name}" + "x" * 100})


def test_empty_template_falls_back_to_default_template():
    validated = validate_application_settings(
        {**BASE_SETTINGS, "display_name_format": "last-first", "display_name_template": "  "})
    assert validated["display_name_template"] == "{last_name}, {first_name}"


def test_default_settings_render_last_first(conn):
    assert DEFAULT_APPLICATION_SETTINGS["display_name_format"] == "last-first"
    add_player(conn, 1, "Juan", "Burgos")
    assert rendered_names(conn) == {1: "Burgos, Juan"}


@pytest.mark.parametrize(
    ("fmt", "template", "expected"),
    [
        ("last-first", "", "Burgos, Juan"),
        ("first-last", "", "Juan Burgos"),
        ("custom", "{first_name} / {last_name}", "Juan / Burgos"),
    ],
)
def test_view_renders_in_configured_format(conn, fmt, template, expected):
    set_format(conn, fmt, template)
    add_player(conn, 1, "Juan", "Burgos")
    assert rendered_names(conn) == {1: expected}


def test_changing_format_changes_rendering_without_touching_stored_data(conn):
    add_player(conn, 1, "Juan", "Burgos")
    set_format(conn, "first-last")
    assert rendered_names(conn) == {1: "Juan Burgos"}
    stored = conn.execute("SELECT first_name, last_name, custom_display_name FROM players").fetchone()
    assert tuple(stored) == ("Juan", "Burgos", None)


def test_custom_display_name_overrides_site_format(conn):
    add_player(conn, 1, "Juan", "Burgos", custom="J. Burgos (ACG)")
    assert rendered_names(conn) == {1: "J. Burgos (ACG)"}
    set_format(conn, "first-last")
    assert rendered_names(conn) == {1: "J. Burgos (ACG)"}


def test_blank_custom_display_name_uses_site_format(conn):
    add_player(conn, 1, "Juan", "Burgos", custom="   ")
    assert rendered_names(conn) == {1: "Burgos, Juan"}


@pytest.mark.parametrize(
    ("fmt", "template"),
    [
        ("last-first", None),
        ("first-last", None),
        ("custom", "{last_name} ({first_name})"),
    ],
)
def test_python_renderer_matches_sql_view(conn, fmt, template):
    set_format(conn, fmt, template or "")
    samples = [("Juan", "Burgos"), ("Ana María", "Lopez Ruiz"), ("Solo", ""), ("", "Only")]
    for index, (first, last) in enumerate(samples, start=1):
        add_player(conn, index, first, last)
    sql_names = rendered_names(conn)
    for index, (first, last) in enumerate(samples, start=1):
        expected = render_display_name(first, last, fmt, template)
        assert sql_names[index] == expected


def test_migration_keeps_hand_edited_names_and_drops_stored_display_name(tmp_path):
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.row_factory = sqlite3.Row
    legacy.execute(
        "CREATE TABLE players (id INTEGER PRIMARY KEY, first_name TEXT NOT NULL, "
        "last_name TEXT NOT NULL, display_name TEXT, country TEXT, club TEXT, "
        "slug TEXT UNIQUE, initial_rating REAL, rating REAL, rd REAL, volatility REAL, "
        "active INTEGER DEFAULT 1)"
    )
    legacy.execute(
        "CREATE INDEX idx_players_display_name ON players (display_name)")
    legacy.execute(
        "CREATE TABLE tournament_pending_players (id INTEGER PRIMARY KEY, "
        "tournament_id INTEGER NOT NULL DEFAULT 1, display_name TEXT NOT NULL, "
        "suggested_name TEXT, rating REAL, rank INTEGER, category TEXT, "
        "source_key TEXT, participating INTEGER DEFAULT 1, created_at TEXT)"
    )
    legacy.execute(
        "INSERT INTO players (id, first_name, last_name, display_name, slug, rating, active) "
        "VALUES (1, 'Juan', 'Burgos', 'Burgos, Juan', 'juan-burgos', 1500, 1)"
    )
    legacy.execute(
        "INSERT INTO players (id, first_name, last_name, display_name, slug, rating, active) "
        "VALUES (2, 'Ana', 'Lopez', 'Dra. Ana Lopez', 'ana-lopez', 1500, 1)"
    )
    legacy.execute(
        "INSERT INTO tournament_pending_players (id, display_name, rating) "
        "VALUES (1, 'Maria Gomez', 1400)"
    )
    legacy.commit()

    ensure_application_settings_table(legacy)
    migrate_display_name_schema(legacy)
    legacy.commit()

    columns = {row[1] for row in legacy.execute("PRAGMA table_info(players)")}
    assert "display_name" not in columns
    assert "custom_display_name" in columns
    pending_columns = {row[1] for row in legacy.execute(
        "PRAGMA table_info(tournament_pending_players)")}
    assert "display_name" not in pending_columns

    customs = dict(legacy.execute(
        "SELECT id, custom_display_name FROM players").fetchall())
    assert customs == {1: None, 2: "Dra. Ana Lopez"}
    assert rendered_names(legacy) == {1: "Burgos, Juan", 2: "Dra. Ana Lopez"}

    pending = legacy.execute(
        "SELECT first_name, last_name FROM tournament_pending_players").fetchone()
    assert tuple(pending) == ("Maria", "Gomez")
    legacy.close()


def test_migration_is_idempotent(conn):
    add_player(conn, 1, "Juan", "Burgos", custom="Custom")
    migrate_display_name_schema(conn)
    migrate_display_name_schema(conn)
    conn.commit()
    assert rendered_names(conn) == {1: "Custom"}


def test_split_display_name_rejects_names_with_several_commas():
    with pytest.raises(ValueError, match="cannot contain commas"):
        split_display_name("Van, Der, Heuvel")


def test_database_rejects_commas_in_first_and_last_names(conn):
    ensure_name_comma_guards(conn)
    with pytest.raises(sqlite3.IntegrityError, match="cannot contain commas"):
        add_player(conn, 1, "Ana, Maria", "Lopez")
    add_player(conn, 2, "Ana", "Lopez")
    with pytest.raises(sqlite3.IntegrityError, match="cannot contain commas"):
        conn.execute("UPDATE players SET last_name = 'Lo, pez' WHERE id = 2")
    with pytest.raises(sqlite3.IntegrityError, match="cannot contain commas"):
        conn.execute(
            "INSERT INTO tournament_pending_players (first_name, last_name, rating, rank) "
            "VALUES ('Ana', 'Lopez, Jr', 1400, 1)"
        )
    conn.execute("UPDATE players SET custom_display_name = 'Ana, la de Lopez' WHERE id = 2")
    conn.commit()
    assert rendered_names(conn) == {2: "Ana, la de Lopez"}


def test_migration_replaces_legacy_name_commas(tmp_path):
    connection = sqlite3.connect(tmp_path / "comma.db")
    connection.row_factory = sqlite3.Row
    create_players_schema(connection)
    ensure_application_settings_table(connection)
    connection.execute(
        "INSERT INTO players (id, first_name, last_name, slug, rating, active) "
        "VALUES (1, 'Ana, Maria', 'Lopez', 'ana-lopez', 1500, 1)"
    )
    connection.commit()
    migrate_display_name_schema(connection)
    connection.commit()
    assert tuple(connection.execute(
        "SELECT first_name, last_name FROM players WHERE id = 1").fetchone()) == ("Ana Maria", "Lopez")
    with pytest.raises(sqlite3.IntegrityError, match="cannot contain commas"):
        connection.execute("UPDATE players SET first_name = 'X, Y' WHERE id = 1")
    connection.close()


def test_migration_links_legacy_suggestions_once_when_the_name_is_unique(tmp_path):
    connection = sqlite3.connect(tmp_path / "suggest.db")
    connection.row_factory = sqlite3.Row
    create_players_schema(connection)
    ensure_application_settings_table(connection)
    connection.executemany(
        "INSERT INTO players (id, first_name, last_name, slug, rating, active) VALUES (?, ?, ?, ?, 1500, 1)",
        [(1, "Ana", "Lopez", "ana-lopez"), (2, "Juan", "Burgos", "juan-a"), (3, "Juan", "Burgos", "juan-b")],
    )
    connection.executemany(
        "INSERT INTO tournament_pending_players (id, first_name, last_name, suggested_name, rating, rank) "
        "VALUES (?, ?, ?, ?, 1400, 1)",
        [(1, "Ana", "Lopez", "Lopez, Ana"), (2, "Juan", "Burgos", "Burgos, Juan"), (3, "Pedro", "Gil", None)],
    )
    connection.commit()
    migrate_display_name_schema(connection)
    connection.commit()
    links = dict(connection.execute(
        "SELECT id, suggested_player_id FROM tournament_pending_players").fetchall())
    assert links == {1: 1, 2: None, 3: None}

    connection.execute("UPDATE players SET active = 0 WHERE id = 3")
    connection.commit()
    migrate_display_name_schema(connection)
    connection.commit()
    relinked = dict(connection.execute(
        "SELECT id, suggested_player_id FROM tournament_pending_players").fetchall())
    assert relinked == {1: 1, 2: None, 3: None}
    connection.close()


def test_settings_page_separates_security_and_display_sections(admin_client):
    response = admin_client.get("/admin/settings?lang=en")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    security = html.index("Security and recovery")
    display = html.index("Name display")
    assert security < html.index('name="max_login_attempts"') < display
    assert display < html.index('name="display_name_format"') < html.index('name="display_name_template"')
    assert html.index('name="display_name_template"') < html.index('class="filter-actions"')
