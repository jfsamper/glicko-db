"""Tests for the Go handicap-stones feature: category math, rating
adjustment, tournament pairing/materialization, OpenGotha XML import, and
the admin form/CSV plumbing."""

import sqlite3
from pathlib import Path

import pytest

import services.category_service as category_service
import services.rating_service as rating_service
import services.import_service as import_service
from services.import_gotha import parse_gotha_xml
from services.tournament_service import (
    create_tournament_from_gotha,
    manual_pair,
    process_tournament_round_matches,
    update_pairing_handicap,
)
from routes.admin import parse_handicap_stones

XML_PATH = Path(__file__).parent.parent / "uploads" / "abierto3-26.xml"
K, M = 16.6, 340.0


# --- category_service --------------------------------------------------

def test_category_value_increases_with_rating():
    assert category_service.category_value(2000, k=K, m=M) > category_service.category_value(1000, k=K, m=M)


def test_handicap_points_is_zero_for_zero_stones():
    assert category_service.handicap_points(1500, 1500, 0, k=K, m=M) == 0.0


def test_handicap_points_scales_linearly_with_stones_and_midpoint_rating():
    one_stone = category_service.handicap_points(1500, 1500, 1, k=K, m=M)
    three_stones = category_service.handicap_points(1500, 1500, 3, k=K, m=M)
    assert three_stones == pytest.approx(one_stone * 3)

    weak_players = category_service.handicap_points(700, 700, 1, k=K, m=M)
    strong_players = category_service.handicap_points(2700, 2700, 1, k=K, m=M)
    assert strong_players > weak_players


def test_suggested_handicap_stones_clamped_and_symmetric():
    assert category_service.suggested_handicap_stones(1500, 1500, k=K, m=M) == 0
    # A large rating gap should clamp at the conventional 9-stone maximum.
    assert category_service.suggested_handicap_stones(3000, 100, k=K, m=M) == 9
    assert category_service.suggested_handicap_stones(100, 100, k=K, m=M) == 0


# --- rating_service ------------------------------------------------------

def test_black_handicap_points_is_noop_when_stones_zero():
    assert rating_service.black_handicap_points(1500, 1400, 0, category_k=K, category_m=M) == 0.0


def test_black_handicap_points_matches_category_service_formula():
    expected = category_service.handicap_points(1600, 1400, 3, k=K, m=M)
    assert rating_service.black_handicap_points(1600, 1400, 3, category_k=K, category_m=M) == expected


def test_handicap_stones_helper_defaults_to_zero_for_missing_or_null_column():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE matches (id INTEGER PRIMARY KEY, result TEXT)")
    conn.execute("INSERT INTO matches (id, result) VALUES (1, '1-0')")
    row = conn.execute("SELECT * FROM matches").fetchone()
    assert rating_service._handicap_stones(row) == 0

    conn.execute("ALTER TABLE matches ADD COLUMN handicap_stones INTEGER")
    row = conn.execute("SELECT * FROM matches").fetchone()
    assert rating_service._handicap_stones(row) == 0
    conn.close()


def test_glicko2_update_handicap_shift_only_affects_opponent_rating():
    baseline = rating_service.glicko2_update(1500, 200, 0.06, 1500, 200, 0.06, 1.0, tau=0.5)
    boosted_opponent = rating_service.glicko2_update(
        1500, 200, 0.06, 1500, 200, 0.06, 1.0, tau=0.5, handicap_points_for_opponent=100.0
    )
    # Beating a nominally-equal opponent who is treated as 100 points stronger
    # should raise the player's rating more than beating a true equal.
    assert boosted_opponent["rating"] > baseline["rating"]


def test_recompute_ratings_applies_symmetric_handicap_adjustment(tmp_path, monkeypatch):
    db_path = tmp_path / "handicap_recompute.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT,
            first_name TEXT,
            last_name TEXT,
            initial_rating REAL,
            rating REAL,
            rd REAL,
            volatility REAL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            round_number INTEGER NOT NULL DEFAULT 0,
            handicap_stones INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            rating REAL NOT NULL,
            rd REAL NOT NULL,
            volatility REAL NOT NULL
        );
        CREATE TABLE rating_config (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            tau REAL, default_rating REAL, default_rd REAL, default_volatility REAL
        );
        CREATE TABLE rating_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            earliest_dirty_date TEXT
        );
        CREATE TABLE category_config (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            glicko_k REAL, glicko_m REAL, updated_at TEXT
        );
        INSERT INTO rating_config (id, tau, default_rating, default_rd, default_volatility) VALUES (1, 0.5, 1500.0, 200.0, 0.06);
        INSERT INTO rating_state (id, earliest_dirty_date) VALUES (1, NULL);
        INSERT INTO category_config (id, glicko_k, glicko_m, updated_at) VALUES (1, 16.6, 340.0, NULL);
        INSERT INTO players (id, display_name, first_name, last_name, initial_rating, rating, rd, volatility, active)
        VALUES (1, 'White Player', 'White', 'Player', 1600, 1600, 200, 0.06, 1);
        INSERT INTO players (id, display_name, first_name, last_name, initial_rating, rating, rd, volatility, active)
        VALUES (2, 'Black Player', 'Black', 'Player', 1400, 1400, 200, 0.06, 1);
        """
    )
    conn.commit()
    conn.close()

    def get_test_db():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(rating_service, "get_db", get_test_db)

    # Baseline: an even game, black (weaker) wins outright.
    conn = get_test_db()
    conn.execute(
        "INSERT INTO matches (match_date, white_player_id, black_player_id, result, handicap_stones) VALUES (?, ?, ?, ?, ?)",
        ("2026-08-01", 1, 2, "0-1", 0),
    )
    conn.commit()
    conn.close()
    rating_service.recompute_ratings()
    baseline_black_rating = get_test_db().execute("SELECT rating FROM players WHERE id = 2").fetchone()[0]

    # Reset and replay the same result, but as a large handicap game.
    conn = get_test_db()
    conn.execute("UPDATE players SET rating = initial_rating, rd = 200, volatility = 0.06")
    conn.execute("UPDATE matches SET handicap_stones = 5 WHERE id = 1")
    conn.commit()
    conn.close()
    rating_service.recompute_ratings()
    handicapped_black_rating = get_test_db().execute("SELECT rating FROM players WHERE id = 2").fetchone()[0]

    # With a handicap subsidizing Black, White is nominally seen as weaker,
    # so Black's win is less surprising and gains fewer rating points.
    assert handicapped_black_rating < baseline_black_rating


# --- routes/admin.py parse_handicap_stones -------------------------------

@pytest.mark.parametrize(
    "raw_value, expected",
    [(None, 0), ("", 0), ("  ", 0), ("0", 0), ("9", 9), ("3", 3)],
)
def test_parse_handicap_stones_accepts_blank_and_in_range_values(raw_value, expected):
    assert parse_handicap_stones(raw_value) == expected


@pytest.mark.parametrize("raw_value", ["-1", "10", "abc", "3.5"])
def test_parse_handicap_stones_rejects_out_of_range_or_non_integer_values(raw_value):
    with pytest.raises(ValueError):
        parse_handicap_stones(raw_value)


# --- OpenGotha XML import: services/import_gotha.py ----------------------

def test_parse_gotha_xml_reads_handicap_attribute(tmp_path):
    xml_path = tmp_path / "handicap_match.xml"
    xml_path.write_text(
        """
        <Tournament>
            <TournamentParameterSet>
                <GeneralParameterSet beginDate="2026-08-01" name="Handicap event"/>
            </TournamentParameterSet>
            <Players>
                <Player firstName="Alice" name="Smith"/>
                <Player firstName="Bob" name="Jones"/>
            </Players>
            <Games>
                <Game roundNumber="1" whitePlayer="SMITHALICE" blackPlayer="JONESBOB" result="RESULT_WHITEWINS" handicap="4"/>
            </Games>
        </Tournament>
        """.strip(),
        encoding="utf-8",
    )

    matches = parse_gotha_xml(xml_path)

    assert len(matches) == 1
    assert matches[0].handicap_stones == 4


def test_parse_gotha_xml_defaults_missing_handicap_attribute_to_zero(tmp_path):
    xml_path = tmp_path / "no_handicap_match.xml"
    xml_path.write_text(
        """
        <Tournament>
            <TournamentParameterSet>
                <GeneralParameterSet beginDate="2026-08-01" name="Even event"/>
            </TournamentParameterSet>
            <Players>
                <Player firstName="Alice" name="Smith"/>
                <Player firstName="Bob" name="Jones"/>
            </Players>
            <Games>
                <Game roundNumber="1" whitePlayer="SMITHALICE" blackPlayer="JONESBOB" result="RESULT_WHITEWINS"/>
            </Games>
        </Tournament>
        """.strip(),
        encoding="utf-8",
    )

    matches = parse_gotha_xml(xml_path)

    assert matches[0].handicap_stones == 0


# --- services/import_service.py import_gotha_xml -------------------------

def _create_minimal_import_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            display_name TEXT NOT NULL,
            country TEXT,
            club TEXT,
            slug TEXT UNIQUE,
            active INTEGER DEFAULT 1,
            rating REAL DEFAULT 1500,
            rd REAL DEFAULT 350,
            volatility REAL DEFAULT 0.06,
            games_played INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            draws INTEGER DEFAULT 0,
            initial_rating REAL,
            last_game_date TEXT
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            event TEXT,
            notes TEXT,
            round_number INTEGER NOT NULL DEFAULT 0,
            handicap_stones INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.commit()
    return conn


def test_import_gotha_xml_persists_handicap_stones(tmp_path, monkeypatch):
    db_path = tmp_path / "gotha_handicap.db"
    _create_minimal_import_db(db_path).close()

    xml_path = tmp_path / "handicap_import.xml"
    xml_path.write_text(
        """
        <Tournament>
            <TournamentParameterSet>
                <GeneralParameterSet beginDate="2026-08-01" name="Handicap event"/>
            </TournamentParameterSet>
            <Players>
                <Player firstName="Alice" name="Smith"/>
                <Player firstName="Bob" name="Jones"/>
            </Players>
            <Games>
                <Game roundNumber="1" whitePlayer="SMITHALICE" blackPlayer="JONESBOB" result="RESULT_WHITEWINS" handicap="2"/>
            </Games>
        </Tournament>
        """.strip(),
        encoding="utf-8",
    )

    def get_test_db():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(import_service, "get_db", get_test_db)
    monkeypatch.setattr(import_service, "mark_dirty", lambda _date: None)

    result = import_service.import_gotha_xml(xml_path)

    assert result["matches"] == 1
    conn = get_test_db()
    handicap_stones = conn.execute("SELECT handicap_stones FROM matches").fetchone()[0]
    conn.close()
    assert handicap_stones == 2


# --- services/tournament_service.py --------------------------------------

def _create_tournament_db():
    conn = sqlite3.connect(":memory:")
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
            name TEXT NOT NULL,
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
            UNIQUE(tournament_id, player_id)
        );
        CREATE TABLE tournament_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            round_number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled',
            UNIQUE(tournament_id, round_number)
        );
        CREATE TABLE tournament_round_players (
            round_id INTEGER, player_id INTEGER, status TEXT,
            UNIQUE(round_id, player_id)
        );
        CREATE TABLE tournament_pairings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER NOT NULL,
            board_number INTEGER NOT NULL,
            white_player_id INTEGER,
            black_player_id INTEGER,
            white_player_name TEXT,
            black_player_name TEXT,
            result TEXT,
            is_bye INTEGER NOT NULL DEFAULT 0,
            handicap_stones INTEGER NOT NULL DEFAULT 0,
            UNIQUE(round_id, board_number)
        );
        CREATE TABLE tournament_pending_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            display_name TEXT NOT NULL,
            suggested_name TEXT,
            resolved_player_id INTEGER,
            rating REAL NOT NULL DEFAULT 0,
            rank INTEGER NOT NULL DEFAULT 0,
            category TEXT NOT NULL DEFAULT '',
            source_key TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE category_config (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            glicko_k REAL, glicko_m REAL, updated_at TEXT
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            event TEXT,
            notes TEXT,
            round_number INTEGER NOT NULL DEFAULT 0,
            tournament_pairing_id INTEGER,
            handicap_stones INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    return conn


def test_manual_pair_auto_suggests_handicap_from_ratings():
    conn = _create_tournament_db()
    conn.execute("INSERT INTO players (id, display_name, rating, active) VALUES (1, 'Strong', 2700, 1)")
    conn.execute("INSERT INTO players (id, display_name, rating, active) VALUES (2, 'Weak', 700, 1)")
    conn.execute("INSERT INTO tournaments (id, name) VALUES (1, 'Test tournament')")
    conn.execute("INSERT INTO tournament_participants (tournament_id, player_id) VALUES (1, 1), (1, 2)")
    conn.execute("INSERT INTO tournament_rounds (id, tournament_id, round_number) VALUES (1, 1, 1)")
    conn.commit()

    manual_pair(conn, 1, 1, 1, 2)

    pairing = conn.execute("SELECT handicap_stones FROM tournament_pairings WHERE round_id = 1").fetchone()
    assert pairing["handicap_stones"] == 9  # clamped to the conventional maximum


def test_manual_pair_accepts_explicit_handicap_override():
    conn = _create_tournament_db()
    conn.execute("INSERT INTO players (id, display_name, rating, active) VALUES (1, 'A', 1600, 1)")
    conn.execute("INSERT INTO players (id, display_name, rating, active) VALUES (2, 'B', 1400, 1)")
    conn.execute("INSERT INTO tournaments (id, name) VALUES (1, 'Test tournament')")
    conn.execute("INSERT INTO tournament_participants (tournament_id, player_id) VALUES (1, 1), (1, 2)")
    conn.execute("INSERT INTO tournament_rounds (id, tournament_id, round_number) VALUES (1, 1, 1)")
    conn.commit()

    manual_pair(conn, 1, 1, 1, 2, handicap_stones=0)

    pairing = conn.execute("SELECT handicap_stones FROM tournament_pairings WHERE round_id = 1").fetchone()
    assert pairing["handicap_stones"] == 0


def test_update_pairing_handicap_validates_range_and_persists():
    conn = _create_tournament_db()
    conn.execute("INSERT INTO players (id, display_name, rating, active) VALUES (1, 'A', 1600, 1)")
    conn.execute("INSERT INTO players (id, display_name, rating, active) VALUES (2, 'B', 1400, 1)")
    conn.execute("INSERT INTO tournaments (id, name) VALUES (1, 'Test tournament')")
    conn.execute("INSERT INTO tournament_participants (tournament_id, player_id) VALUES (1, 1), (1, 2)")
    conn.execute("INSERT INTO tournament_rounds (id, tournament_id, round_number) VALUES (1, 1, 1)")
    conn.commit()
    manual_pair(conn, 1, 1, 1, 2, handicap_stones=0)
    pairing_id = conn.execute("SELECT id FROM tournament_pairings WHERE round_id = 1").fetchone()[0]

    update_pairing_handicap(conn, 1, pairing_id, 6)
    assert conn.execute(
        "SELECT handicap_stones FROM tournament_pairings WHERE id = ?", (pairing_id,)
    ).fetchone()[0] == 6

    with pytest.raises(ValueError):
        update_pairing_handicap(conn, 1, pairing_id, 10)
    with pytest.raises(ValueError):
        update_pairing_handicap(conn, 1, pairing_id, -1)
    with pytest.raises(ValueError):
        update_pairing_handicap(conn, 1, pairing_id, "not-a-number")


def test_process_tournament_round_matches_carries_handicap_into_matches_table():
    conn = _create_tournament_db()
    conn.execute("INSERT INTO players (id, display_name, rating, active) VALUES (1, 'A', 1600, 1)")
    conn.execute("INSERT INTO players (id, display_name, rating, active) VALUES (2, 'B', 1400, 1)")
    conn.execute("INSERT INTO tournaments (id, name) VALUES (1, 'Test tournament')")
    conn.execute("INSERT INTO tournament_participants (tournament_id, player_id) VALUES (1, 1), (1, 2)")
    conn.execute("INSERT INTO tournament_rounds (id, tournament_id, round_number) VALUES (1, 1, 1)")
    conn.commit()
    manual_pair(conn, 1, 1, 1, 2, handicap_stones=4)
    pairing_id = conn.execute("SELECT id FROM tournament_pairings WHERE round_id = 1").fetchone()[0]
    conn.execute("UPDATE tournament_pairings SET result = '1-0' WHERE id = ?", (pairing_id,))
    conn.commit()

    process_tournament_round_matches(conn, 1, round_id=1)

    match_handicap = conn.execute(
        "SELECT handicap_stones FROM matches WHERE tournament_pairing_id = ?", (pairing_id,)
    ).fetchone()[0]
    assert match_handicap == 4


def test_create_tournament_from_gotha_imports_handicap_from_xml(tmp_path):
    conn = _create_tournament_db()
    source = XML_PATH.read_text(encoding="utf-8")
    source = source.replace(
        'blackPlayer="LARAJOSELUIS" handicap="0"',
        'blackPlayer="LARAJOSELUIS" handicap="3"',
        1,
    )
    xml_path = tmp_path / "abierto3-26-handicap.xml"
    xml_path.write_text(source, encoding="utf-8")

    tournament_id, _, _ = create_tournament_from_gotha(conn, xml_path, "swiss")

    stones = [
        row[0]
        for row in conn.execute(
            """
            SELECT p.handicap_stones
            FROM tournament_pairings p
            JOIN tournament_rounds r ON r.id = p.round_id
            WHERE r.tournament_id = ?
            """,
            (tournament_id,),
        ).fetchall()
    ]
    assert stones.count(3) == 1
    assert all(stone in (0, 3) for stone in stones)
