import importlib.util
import sqlite3
import warnings
from pathlib import Path
import tempfile
import textwrap
from datetime import datetime
from zoneinfo import ZoneInfo

import services.common as common
import services.helpers as helpers
import services.import_gotha as import_gotha
import services.rating_service as rating_service


def test_application_clock_uses_fixed_utc_minus_five_by_default():
    current = common.current_datetime()

    assert current.utcoffset().total_seconds() == -5 * 60 * 60
    assert common.current_date() == current.date()
    assert common.current_timestamp() == current.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def test_timezone_choices_are_unique_sorted_and_prefer_requested_regions():
    choices = common.get_timezone_choices()
    offsets = [datetime.now(ZoneInfo(timezone_name)).utcoffset() for timezone_name in choices]

    assert len(choices) == len(set(offsets))
    assert offsets == sorted(offsets)
    assert choices[choices.index("America/Mexico_City") + 1 : choices.index("UTC")] == (
        "America/Bogota",
        "America/Caracas",
        "America/Sao_Paulo",
    )
    assert "Asia/Shanghai" in choices
    assert choices[choices.index("Asia/Shanghai") + 1] == "Asia/Seoul"
    assert "Asia/Tokyo" not in choices


def test_application_clock_uses_account_timezone_and_falls_back_for_invalid_values(monkeypatch, tmp_path):
    db_path = tmp_path / "timezone.db"
    monkeypatch.setattr(common, "DB_PATH", str(db_path))
    conn = sqlite3.connect(db_path)
    common.migrate_auth_schema(conn)
    user_id = conn.execute(
        "INSERT INTO users (username, password_hash, timezone) VALUES (?, ?, ?)",
        ("timezone-user", "unused", "America/New_York"),
    ).lastrowid
    conn.commit()

    eastern = common.current_datetime(user_id)
    assert eastern.tzname() in {"EDT", "EST"}
    assert eastern.utcoffset().total_seconds() == -4 * 60 * 60

    conn.execute("UPDATE users SET timezone = ? WHERE id = ?", ("Not/AZone", user_id))
    conn.commit()
    assert common.current_datetime(user_id).utcoffset().total_seconds() == -5 * 60 * 60
    conn.close()


def test_rating_recompute_orders_same_day_matches_by_round(monkeypatch, tmp_path):
    db_path = tmp_path / "round_order_test.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            initial_rating REAL,
            rating REAL,
            rd REAL,
            volatility REAL
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            round_number INTEGER
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            rating REAL NOT NULL,
            rd REAL NOT NULL,
            volatility REAL NOT NULL
        );
        CREATE TABLE rating_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            earliest_dirty_date TEXT
        );
        INSERT INTO rating_state VALUES (1, NULL);
        INSERT INTO players VALUES
            (1, 'Alice', 1500, 1500, 350, 0.06),
            (2, 'Bob', 1500, 1500, 350, 0.06),
            (3, 'Charlie', 1500, 1500, 350, 0.06);
        INSERT INTO matches (match_date, white_player_id, black_player_id, result, round_number) VALUES
            ('2026-08-01', 1, 2, '1-0', 2),
            ('2026-08-01', 1, 3, '0-1', 1),
            ('2026-08-01', 2, 3, '1/2-1/2', 0);
        """
    )
    conn.commit()
    conn.close()

    def get_test_db():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    calls = []

    def record_update(rating, rd, vol, opponent_rating, opponent_rd, opponent_vol, score, conn=None, tau=None, **kwargs):
        calls.append(score)
        return {"rating": rating, "rd": rd, "volatility": vol}

    monkeypatch.setattr(rating_service, "get_db", get_test_db)
    monkeypatch.setattr(rating_service, "glicko2_update", record_update)

    rating_service.recompute_ratings()

    assert calls == [0.0, 1.0, 0.5, 0.5, 1.0, 0.0]


def test_header_index_rejects_ambiguous_substring_matches():
    assert helpers.header_index(["Date"], ["date"]) == 0
    assert helpers.header_index(["Tournament Date", "Match Date"], ["date"]) is None


def test_slugify_uses_a_deterministic_fallback_for_empty_or_symbolic_values():
    assert helpers.slugify("Alice Smith") == "alice-smith"
    assert helpers.slugify("") != "player"
    assert helpers.slugify("!!!").startswith("player-")
    assert helpers.slugify("!!!") != helpers.slugify("???")


def test_dev_pairing_script_exposes_supported_cli_and_helpers():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "dev_only" / "create_pairing_test_tournaments.py"
    spec = importlib.util.spec_from_file_location("dev_pairing_script", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "main")
    assert hasattr(module, "build_plan")
    assert hasattr(module, "create_tournament")
    assert callable(module.main)
    assert callable(module.build_plan)
    assert callable(module.create_tournament)


def test_split_name_handles_names_with_and_without_commas():
    assert helpers.split_name("Ada Lovelace") == ("Ada", "Lovelace")
    assert helpers.split_name("Lovelace, Ada") == ("Ada", "Lovelace")


def test_normalize_round_note_standardizes_legacy_round_text():
    assert helpers.normalize_round_note("Round 3") == 3
    assert helpers.normalize_round_note("Ronda 3") == 3
    assert helpers.normalize_round_note("3") == 3
    assert helpers.normalize_round_note("round 03") == 3
    assert helpers.normalize_round_note("15:00:00") == 3
    assert helpers.normalize_round_note("14:00:00") == 2
    assert helpers.normalize_round_note("2 p.m.") == 2
    assert helpers.normalize_round_note("Adjourned game") == 0
    assert helpers.normalize_round_note("Ronda de Desempate") == 0


def test_sql_round_normalizer_keeps_original_note_text_for_display():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE matches (notes TEXT)")
    conn.executemany(
        "INSERT INTO matches (notes) VALUES (?)",
        [
            ("Round 3",),
            ("Unknown note",),
            ("",),
            (None,),
            ("14:00:00",),
        ],
    )
    conn.create_function("normalize_round_note_sql", 1, helpers.normalize_round_note_sql)

    rows = conn.execute(
        """
        SELECT notes, normalize_round_note_sql(notes) AS round_number
        FROM matches
        ORDER BY round_number ASC, notes COLLATE NOCASE
        """
    ).fetchall()

    assert rows[0]["notes"] is None
    assert rows[0]["round_number"] == 0
    assert rows[1]["notes"] == ""
    assert rows[1]["round_number"] == 0
    assert rows[2]["notes"] == "Unknown note"
    assert rows[2]["round_number"] == 0
    assert rows[3]["notes"] == "14:00:00"
    assert rows[3]["round_number"] == 2
    assert rows[4]["notes"] == "Round 3"
    assert rows[4]["round_number"] == 3


def test_export_players_exposes_a_main_entrypoint():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "export_players.py"
    spec = importlib.util.spec_from_file_location("export_players", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "main")
    assert callable(module.main)


def test_import_gotha_parses_winner_metadata_for_decisive_games():
    xml = textwrap.dedent(
        """
        <Tournament>
          <TournamentParameterSet>
            <GeneralParameterSet beginDate="2024-02-10" name="Demo event"/>
          </TournamentParameterSet>
          <Players>
            <Player firstName="Alice" name="Smith"/>
            <Player firstName="Bob" name="Jones"/>
          </Players>
          <Games>
            <Game roundNumber="1" whitePlayer="SMITHALICE" blackPlayer="JONESBOB" result="RESULT_WHITEWINS"/>
          </Games>
        </Tournament>
        """
    ).strip()

    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as handle:
        handle.write(xml)
        path = handle.name

    try:
        matches = import_gotha.parse_gotha_xml(path)
        assert isinstance(matches[0], import_gotha.GothaMatch)
        assert matches[0].winner == matches[0]["winner"] == "Smith, Alice"
        assert matches[0]["result"] == "1-0"
    finally:
        Path(path).unlink(missing_ok=True)


def test_refresh_stats_aggregates_results_and_includes_inactive_players(monkeypatch, tmp_path):
    db_path = tmp_path / "ratings.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            games_played INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            draws INTEGER DEFAULT 0
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            white_player_id INTEGER,
            black_player_id INTEGER,
            result TEXT
        );
        """
    )
    conn.executemany("INSERT INTO players (id) VALUES (?)", [(1,), (2,), (3,), (4,)])
    conn.executemany(
        "INSERT INTO matches VALUES (?, ?, ?, ?)",
        [
            (1, 1, 2, "1-0"),
            (2, 3, 1, "1/2-1/2"),
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(common, "get_db", lambda: sqlite3.connect(db_path))
    common.refresh_stats()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, games_played, wins, losses, draws FROM players ORDER BY id"
    ).fetchall()
    conn.close()

    assert rows == [
        (1, 2, 1, 0, 1),
        (2, 1, 0, 1, 0),
        (3, 1, 0, 0, 1),
        (4, 0, 0, 0, 0),
    ]


def test_mark_dirty_keeps_the_earliest_affected_match_date(monkeypatch, tmp_path):
    db_path = tmp_path / "ratings.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE rating_state (id INTEGER PRIMARY KEY, earliest_dirty_date TEXT)"
    )
    conn.execute("INSERT INTO rating_state VALUES (1, NULL)")
    conn.commit()
    conn.close()

    def get_test_db():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(rating_service, "get_db", get_test_db)

    rating_service.mark_dirty("2026-06-15")
    rating_service.mark_dirty("2026-05-01")

    assert rating_service.get_dirty_date() == "2026-05-01"


def test_mark_dirty_handles_empty_state_and_same_date_without_changing_it(monkeypatch, tmp_path):
    db_path = tmp_path / "ratings.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE rating_state (id INTEGER PRIMARY KEY, earliest_dirty_date TEXT)")
    conn.execute("INSERT INTO rating_state VALUES (1, '2026-05-01')")
    conn.commit()
    conn.close()

    def get_test_db():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(rating_service, "get_db", get_test_db)

    rating_service.mark_dirty("2026-05-01")
    rating_service.mark_dirty("2026-06-01")
    assert rating_service.get_dirty_date() == "2026-05-01"


def test_clear_dirty_date_is_idempotent(monkeypatch, tmp_path):
    db_path = tmp_path / "ratings.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE rating_state (id INTEGER PRIMARY KEY, earliest_dirty_date TEXT)")
    conn.execute("INSERT INTO rating_state VALUES (1, '2026-05-01')")
    conn.commit()
    conn.close()

    def get_test_db():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(rating_service, "get_db", get_test_db)

    rating_service.clear_dirty_date()
    rating_service.clear_dirty_date()
    assert rating_service.get_dirty_date() is None


def test_update_from_latest_snapshot_matches_full_recompute_ratings(monkeypatch, tmp_path):
    """Verify incremental rating replay produces identical ratings/snapshots to full recomputation."""
    db_path = tmp_path / "replay_test.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT, last_name TEXT, display_name TEXT,
            initial_rating REAL, rating REAL DEFAULT 1500, rd REAL DEFAULT 350, volatility REAL DEFAULT 0.06
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            event TEXT, notes INTEGER
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            rating REAL NOT NULL,
            rd REAL NOT NULL,
            volatility REAL NOT NULL
        );
        CREATE TABLE rating_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            earliest_dirty_date TEXT
        );
        CREATE TABLE rating_config (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            tau REAL, default_rating REAL, default_rd REAL, default_volatility REAL, updated_at TEXT
        );
        INSERT INTO rating_state VALUES (1, NULL);
        INSERT INTO rating_config VALUES (1, 0.5, 1500.0, 350.0, 0.06, CURRENT_TIMESTAMP);
        INSERT INTO players (id, display_name, initial_rating, rating, rd, volatility) VALUES
            (1, 'Alice', 1500.0, 1500.0, 350.0, 0.06),
            (2, 'Bob', 1500.0, 1500.0, 350.0, 0.06),
            (3, 'Charlie', 1500.0, 1500.0, 350.0, 0.06);
        INSERT INTO matches (match_date, white_player_id, black_player_id, result) VALUES
            ('2026-01-01', 1, 2, '1-0'),
            ('2026-01-02', 2, 3, '1-0'),
            ('2026-01-04', 1, 3, '1-0');
        """
    )
    conn.commit()
    conn.close()

    def get_test_db():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(rating_service, "get_db", get_test_db)

    # Initial full recompute
    rating_service.recompute_ratings()

    # Insert backdated match on 2026-01-03 between Bob and Alice
    conn = get_test_db()
    conn.execute(
        "INSERT INTO matches (match_date, white_player_id, black_player_id, result) VALUES ('2026-01-03', 2, 1, '1-0')"
    )
    conn.commit()
    conn.close()

    # Mark dirty and run incremental replay
    rating_service.mark_dirty("2026-01-03")
    assert rating_service.get_dirty_date() == "2026-01-03"

    rating_service.update_from_latest_snapshot()
    assert rating_service.get_dirty_date() is None

    conn = get_test_db()
    incremental_players = conn.execute("SELECT id, rating, rd, volatility FROM players ORDER BY id").fetchall()
    incremental_snapshots = conn.execute("SELECT player_id, snapshot_date, rating, rd, volatility FROM rating_snapshots ORDER BY snapshot_date, id").fetchall()
    conn.close()

    # Now run full recompute from scratch
    rating_service.recompute_ratings()

    conn = get_test_db()
    full_players = conn.execute("SELECT id, rating, rd, volatility FROM players ORDER BY id").fetchall()
    full_snapshots = conn.execute("SELECT player_id, snapshot_date, rating, rd, volatility FROM rating_snapshots ORDER BY snapshot_date, id").fetchall()
    conn.close()

    assert [dict(r) for r in incremental_players] == [dict(r) for r in full_players]
    assert [dict(r) for r in incremental_snapshots] == [dict(r) for r in full_snapshots]
