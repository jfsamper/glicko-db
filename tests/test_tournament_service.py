import random
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

from config import GLICKO_M
from scripts.dev_only.create_pairing_test_tournaments import (
    PLAYER_COUNTS,
    DEFAULT_ROUNDS as ROUNDS,
    DEFAULT_SEED as SEED,
    create_tournament as create_pairing_test_tournament,
)
from services.tournament_service import (
    _materialize_pending_players,
    _pairing_policy,
    _participant_state,
    _recalculate_mcmahon_seeds,
    _suggest_player_name,
    add_participant,
    create_tournament_from_gotha,
    delete_tournament,
    export_tournament_results,
    generate_next_round,
    get_tournament_standings,
    list_tournament_participants,
    manual_pair,
    normalize_tournament_rounds,
    pair_selected_players,
    process_tournament_round_matches,
    remove_participant,
    read_gotha_tournament,
    _refresh_tournament_completion_state,
    save_tournament_matches,
    set_pairing_result,
    set_round_player_status,
    sync_match_pairing,
    sync_tournament_matches,
    unpair,
    update_pairing,
)
from services.helpers import normalize_key
from services.import_gotha import GothaPlayer, GothaTournamentPayload


XML_PATH = Path(__file__).parents[1] / "uploads" / "abierto3-26.xml"
FRENCH_XML_PATH = Path(__file__).parents[1] / "uploads" / "french-example.xml"


def create_db():
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


def seed_players(conn):
    metadata = read_gotha_tournament(XML_PATH)
    for index, player in enumerate(metadata["players"], 1):
        first_name, last_name = player["display_name"].rsplit(" ", 1)
        conn.execute(
            "INSERT INTO players VALUES (?, ?, ?, ?, ?, 1)",
            (index, first_name, last_name, player["display_name"], player["rating"]),
        )
    conn.commit()


def create_manual_tournament(conn, rounds=5, pairing_system="swiss", name="Manual tournament"):
    conn.execute(
        "INSERT INTO tournaments (name, rounds, pairing_system, tournament_type) VALUES (?, ?, ?, ?)",
        (name, rounds, pairing_system, pairing_system),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_suggest_player_name_uses_token_overlap_for_reordered_names():
    conn = create_db()
    conn.execute(
        "INSERT INTO players (id, first_name, last_name, display_name, rating, active) VALUES (?, ?, ?, ?, ?, ?)",
        (1, "Juan Felipe", "Burgos", "Juan Felipe Burgos", 1500, 1),
    )
    conn.execute(
        "INSERT INTO players (id, first_name, last_name, display_name, rating, active) VALUES (?, ?, ?, ?, ?, ?)",
        (2, "Brandal", "Henao", "Brandal Henao", 1500, 1),
    )
    conn.execute(
        "INSERT INTO players (id, first_name, last_name, display_name, rating, active) VALUES (?, ?, ?, ?, ?, ?)",
        (3, "Santiago", "Espinosa", "Santiago Espinosa", 1500, 1),
    )
    conn.commit()

    assert _suggest_player_name("Burgos Juan", conn) == "Juan Felipe Burgos"
    assert _suggest_player_name("Henao Brandal", conn) == "Brandal Henao"
    assert _suggest_player_name("Espinosa Santiago", conn) == "Santiago Espinosa"


def test_acceleration_policy_uses_rating_seed_and_expires_after_opening_rounds():
    conn = create_db()
    conn.execute("ALTER TABLE tournaments ADD COLUMN acceleration_rounds INTEGER NOT NULL DEFAULT 2")
    conn.execute("ALTER TABLE tournaments ADD COLUMN category_rounds INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE tournaments ADD COLUMN acceleration_scheme TEXT NOT NULL DEFAULT '50:1,25:0.5,25:0'")
    tournament_id = create_manual_tournament(conn, rounds=4, pairing_system="accelerated_swiss")
    ratings = (1200, 1500, 1800, 2100)
    for player_id, rating in enumerate(ratings, 1):
        conn.execute(
            "INSERT INTO players (id, first_name, last_name, display_name, rating, active) VALUES (?, ?, ?, ?, ?, 1)",
            (player_id, "First", str(player_id), f"First {player_id}", rating),
        )
    for player_id in reversed(range(1, 5)):
        add_participant(conn, tournament_id, player_id)
    conn.commit()

    tournament = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    assert _pairing_policy(tournament, 1)["acceleration_active"] is True
    assert _pairing_policy(tournament, 3)["acceleration_active"] is False
    opening_state = _participant_state(
        conn,
        tournament_id,
        acceleration_scheme=tournament["acceleration_scheme"],
        acceleration_active=True,
    )
    later_state = _participant_state(
        conn,
        tournament_id,
        acceleration_scheme=tournament["acceleration_scheme"],
        acceleration_active=False,
    )
    assert opening_state[4]["acceleration"] == 1.0
    assert opening_state[1]["acceleration"] == 0.0
    assert all(player["acceleration"] == 0.0 for player in later_state.values())


def test_accelerated_standings_drop_virtual_points_after_acceleration_rounds():
    conn = create_db()
    conn.execute("ALTER TABLE tournaments ADD COLUMN acceleration_rounds INTEGER NOT NULL DEFAULT 2")
    conn.execute("ALTER TABLE tournaments ADD COLUMN acceleration_scheme TEXT NOT NULL DEFAULT '50:1,25:0.5,25:0'")
    tournament_id = create_manual_tournament(conn, rounds=3, pairing_system="accelerated_swiss")
    for player_id, rating in enumerate((1200, 1500, 1800, 2100), 1):
        conn.execute(
            "INSERT INTO players (id, first_name, last_name, display_name, rating, active) VALUES (?, ?, ?, ?, ?, 1)",
            (player_id, "First", str(player_id), f"First {player_id}", rating),
        )
    for player_id in range(1, 5):
        add_participant(conn, tournament_id, player_id)
    conn.commit()

    for expected_round in (1, 2, 3):
        round_id, pairings = generate_next_round(conn, tournament_id)
        assert conn.execute(
            "SELECT round_number FROM tournament_rounds WHERE id = ?", (round_id,)
        ).fetchone()[0] == expected_round
        conn.execute(
            "UPDATE tournament_pairings SET result = '1-0' WHERE round_id = ? AND is_bye = 0",
            (round_id,),
        )
        conn.commit()
        standings = get_tournament_standings(conn, tournament_id)
        top_seed = next(row for row in standings if row["id"] == 4)
        expected_virtual = 1.0 if expected_round <= 2 else 0.0
        assert top_seed["primary_score"] - top_seed["score"] == expected_virtual


def test_opengotha_import_persists_fuzzy_player_suggestion():
    conn = create_db()
    conn.execute(
        "INSERT INTO players (id, first_name, last_name, display_name, rating, active) VALUES (?, ?, ?, ?, ?, ?)",
        (1, "Juan Felipe", "Burgos", "Juan Felipe Burgos", 2400, 1),
    )
    conn.commit()

    tournament_id, _, _ = create_tournament_from_gotha(
        conn,
        Path(__file__).parents[1] / "uploads" / "ABIERTO II-2024.xml",
        "swiss",
    )

    pending = conn.execute(
        """
        SELECT display_name, suggested_name
        FROM tournament_pending_players
        WHERE tournament_id = ? AND display_name = ?
        """,
        (tournament_id, "Burgos Juan"),
    ).fetchone()

    assert pending is not None
    assert pending["suggested_name"] == "Juan Felipe Burgos"


def test_opengotha_import_does_not_create_missing_players_until_processing():
    conn = create_db()
    seed_players(conn)
    conn.execute("DELETE FROM players WHERE id = 3")

    tournament_id, metadata, matched = create_tournament_from_gotha(conn, XML_PATH, "swiss")

    assert conn.execute(
        "SELECT status FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()[0] == "draft"
    assert matched == 9
    assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 9
    assert conn.execute("SELECT COUNT(*) FROM tournament_pending_players WHERE tournament_id = ?", (tournament_id,)).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM tournament_participants WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone()[0] == 9
    assert conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE round_id IN (SELECT id FROM tournament_rounds WHERE tournament_id = ?)",
        (tournament_id,),
    ).fetchone()[0] == 25
    assert conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE white_player_name IS NOT NULL OR black_player_name IS NOT NULL",
    ).fetchone()[0] >= 1

    round_id = conn.execute(
        "SELECT id FROM tournament_rounds WHERE tournament_id = ? ORDER BY round_number LIMIT 1",
        (tournament_id,),
    ).fetchone()[0]
    inserted = process_tournament_round_matches(conn, tournament_id, round_id=round_id)
    assert inserted > 0
    assert conn.execute("SELECT COUNT(*) FROM players WHERE active = 1").fetchone()[0] >= 10
    assert conn.execute("SELECT COUNT(*) FROM tournament_pending_players WHERE tournament_id = ?", (tournament_id,)).fetchone()[0] == 0


def test_opengotha_metadata_and_pairing_parameters_are_read():
    metadata = read_gotha_tournament(XML_PATH)

    assert metadata["name"] == "III Abierto Nacional 2026"
    assert metadata["rounds"] == 5
    assert len(metadata["players"]) == 10
    assert metadata["pairing_parameters"]["paiBaDeterministic"] == "true"
    assert metadata["bye_points"] == 1
    assert metadata["absent_points"] == 0
    assert metadata["placement_criteria"] == "NBW,SOSW,SOSOSW,NULL,NULL,NULL"
    assert metadata["pairing_system"] == "swiss"


def test_opengotha_metadata_uses_typed_payload_with_mapping_compatibility():
    metadata = read_gotha_tournament(XML_PATH)

    assert isinstance(metadata, GothaTournamentPayload)
    assert isinstance(metadata.players[0], GothaPlayer)
    assert metadata.name == metadata["name"] == "III Abierto Nacional 2026"
    assert metadata.players[0].display_name == metadata.players[0]["display_name"]

    metadata.update({"name": "Imported event"})
    assert metadata.name == metadata["name"] == "Imported event"


def test_opengotha_mcmahon_mm_values_are_read_when_criteria_are_trimmed_or_forced(tmp_path):
    source = (Path(__file__).parents[1] / "uploads" / "mms-example.xml").read_text(encoding="utf-8")
    xml_path = tmp_path / "mcmahon_variant.xml"
    xml_path.write_text(source.replace('name="MMS"', 'name="mms "'), encoding="utf-8")

    metadata = read_gotha_tournament(xml_path, pairing_system="mcmahon")

    assert metadata["tournament_type"] == "mcmahon"
    assert metadata["pairing_system"] == "mcmahon"
    assert metadata["mm_bar"] == 3
    assert metadata["mm_floor"] == -20
    assert metadata["mm_zero"] == 30


def test_opengotha_ratings_never_fall_below_glicko_floor():
    metadata = read_gotha_tournament(Path(__file__).parents[1] / "uploads" / "OGSTest.xml")

    akita = next(player for player in metadata["players"] if player["display_name"] == "Akita Noek")

    assert akita["rating"] == GLICKO_M
    assert all(player["rating"] >= GLICKO_M for player in metadata["players"])


def test_opengotha_import_creates_accelerated_tournament_and_participants():
    conn = create_db()
    seed_players(conn)

    tournament_id, metadata, matched = create_tournament_from_gotha(
        conn, XML_PATH, "accelerated_swiss"
    )

    assert matched == 10
    tournament = conn.execute(
        "SELECT name, pairing_system, source_format, status FROM tournaments WHERE id = ?",
        (tournament_id,),
    ).fetchone()
    assert tuple(tournament) == ("III Abierto Nacional 2026", "accelerated_swiss", "OpenGotha XML", "draft")
    assert conn.execute(
        "SELECT COUNT(*) FROM tournament_participants WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone()[0] == 10


def test_opengotha_import_persists_rounds_pairings_and_results():
    conn = create_db()
    seed_players(conn)

    tournament_id, _, _ = create_tournament_from_gotha(conn, XML_PATH, "swiss")

    assert conn.execute(
        "SELECT COUNT(*) FROM tournament_rounds WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone()[0] == 5
    assert conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE round_id IN (SELECT id FROM tournament_rounds WHERE tournament_id = ?)",
        (tournament_id,),
    ).fetchone()[0] == 25
    assert conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE round_id IN (SELECT id FROM tournament_rounds WHERE tournament_id = ?) AND result IS NOT NULL",
        (tournament_id,),
    ).fetchone()[0] == 25


def test_opengotha_import_marks_empty_black_player_as_bye(tmp_path):
    conn = create_db()
    seed_players(conn)
    source = XML_PATH.read_text(encoding="utf-8")
    source = source.replace('blackPlayer="LARAJOSELUIS"', 'blackPlayer=""', 1)
    xml_path = tmp_path / "import-with-bye.xml"
    xml_path.write_text(source, encoding="utf-8")

    tournament_id, _, _ = create_tournament_from_gotha(conn, xml_path, "swiss")

    pairing = conn.execute(
        """
        SELECT white_player_id, black_player_id, is_bye
        FROM tournament_pairings
        WHERE round_id = (SELECT id FROM tournament_rounds WHERE tournament_id = ? AND round_number = 1)
        ORDER BY board_number
        LIMIT 1
        """,
        (tournament_id,),
    ).fetchone()
    assert pairing["black_player_id"] is None
    assert pairing["is_bye"] == 1
    assert conn.execute(
        "SELECT received_bye FROM tournament_participants WHERE tournament_id = ? AND player_id = ?",
        (tournament_id, pairing["white_player_id"]),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT status FROM tournament_round_players WHERE player_id = ? AND round_id = (SELECT id FROM tournament_rounds WHERE tournament_id = ? AND round_number = 1)",
        (pairing["white_player_id"], tournament_id),
    ).fetchone()[0] == "bye"


def test_next_round_persists_pairings_and_avoids_repeats():
    conn = create_db()
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=5, pairing_system="swiss")

    for player_id in range(1, 11):
        add_participant(conn, tournament_id, player_id)

    first_round_id, first_pairings = generate_next_round(conn, tournament_id)
    second_round_id, second_pairings = generate_next_round(conn, tournament_id)

    assert first_round_id != second_round_id
    assert len(first_pairings) == 5
    assert len(second_pairings) == 5
    first_pairs = {
        frozenset((pairing["white_player_id"], pairing["black_player_id"]))
        for pairing in first_pairings
        if not pairing["is_bye"]
    }
    second_pairs = {
        frozenset((pairing["white_player_id"], pairing["black_player_id"]))
        for pairing in second_pairings
        if not pairing["is_bye"]
    }
    assert first_pairs.isdisjoint(second_pairs)
    assert conn.execute("SELECT COUNT(*) FROM tournament_rounds").fetchone()[0] == 2


def test_manual_mcmahon_participants_get_seed_initial_scores():
    conn = create_db()
    seed_players(conn)
    conn.execute(
        "INSERT INTO tournaments (name, rounds, pairing_system, tournament_type) VALUES (?, ?, ?, ?)",
        ("Manual McMahon", 3, "mcmahon", "mcmahon"),
    )
    tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    for player_id in (1, 2, 3):
        add_participant(conn, tournament_id, player_id)

    rows = conn.execute(
        "SELECT player_id, initial_score FROM tournament_participants WHERE tournament_id = ? ORDER BY seed_rank",
        (tournament_id,),
    ).fetchall()

    assert [(row["player_id"], row["initial_score"]) for row in rows] == [
        (1, 8.0),
        (2, 7.0),
        (3, 6.0),
    ]
    assert conn.execute(
        "SELECT category FROM tournament_participants WHERE tournament_id = ? ORDER BY seed_rank",
        (tournament_id,),
    ).fetchall()[0]["category"]


def test_new_manual_mcmahon_offset_covers_participant_count():
    conn = create_db()
    conn.execute("ALTER TABLE tournaments ADD COLUMN mm_bar INTEGER DEFAULT 8")
    conn.execute("ALTER TABLE tournaments ADD COLUMN mm_floor INTEGER DEFAULT -30")
    conn.execute("ALTER TABLE tournaments ADD COLUMN mm_zero INTEGER DEFAULT 0")
    seed_players(conn)
    conn.execute(
        "INSERT INTO tournaments (name, rounds, pairing_system, tournament_type) VALUES (?, ?, ?, ?)",
        ("Manual McMahon", 3, "mcmahon", "mcmahon"),
    )
    tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    for player_id in range(1, 11):
        add_participant(conn, tournament_id, player_id)

    tournament = conn.execute(
        "SELECT mm_zero FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()
    scores = conn.execute(
        "SELECT initial_score FROM tournament_participants WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchall()

    assert tournament["mm_zero"] == 10
    assert min(row["initial_score"] for row in scores) >= 0


def test_materialized_pending_mcmahon_players_keep_seed_initial_scores():
    conn = create_db()
    conn.execute("ALTER TABLE tournaments ADD COLUMN mm_bar INTEGER DEFAULT 8")
    conn.execute("ALTER TABLE tournaments ADD COLUMN mm_floor INTEGER DEFAULT -30")
    conn.execute("ALTER TABLE tournaments ADD COLUMN mm_zero INTEGER DEFAULT 30")
    conn.execute(
        "INSERT INTO tournaments (name, rounds, pairing_system, tournament_type, mm_bar, mm_floor, mm_zero) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Pending McMahon", 4, "mcmahon", "mcmahon", 3, -20, 30),
    )
    tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.executemany(
        "INSERT INTO tournament_pending_players (tournament_id, display_name, suggested_name, rating, rank, category, source_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (tournament_id, "Alice Alpha", None, 1500.0, 1, "3K", "ALPHAALICE"),
            (tournament_id, "Bob Bravo", None, 1450.0, 2, "5K", "BRAVOBOB"),
            (tournament_id, "Carla Charlie", None, 1400.0, 3, "6K", "CHARLIECARLA"),
        ],
    )
    conn.commit()

    created = _materialize_pending_players(conn, tournament_id)
    assert created == 3

    rows = conn.execute(
        "SELECT p.display_name, tp.seed_rank, tp.initial_score FROM tournament_participants tp JOIN players p ON p.id = tp.player_id WHERE tp.tournament_id = ? ORDER BY tp.seed_rank",
        (tournament_id,),
    ).fetchall()
    assert [(row["display_name"], row["seed_rank"], row["initial_score"]) for row in rows] == [
        ("Alice Alpha", 1, 33.0),
        ("Bob Bravo", 2, 32.0),
        ("Carla Charlie", 3, 31.0),
    ]


def test_french_mcmahon_standings_assign_mms_to_pending_players():
    conn = create_db()
    conn.execute("ALTER TABLE tournaments ADD COLUMN mm_bar INTEGER DEFAULT 8")
    conn.execute("ALTER TABLE tournaments ADD COLUMN mm_floor INTEGER DEFAULT -30")
    conn.execute("ALTER TABLE tournaments ADD COLUMN mm_zero INTEGER DEFAULT 30")

    metadata = read_gotha_tournament(FRENCH_XML_PATH)
    top_player = metadata["players"][0]
    first_name, last_name = top_player["display_name"].rsplit(" ", 1)
    conn.execute(
        "INSERT INTO players (id, first_name, last_name, display_name, rating, active) VALUES (?, ?, ?, ?, ?, 1)",
        (1, first_name, last_name, top_player["display_name"], top_player["rating"]),
    )
    conn.commit()

    tournament_id, imported_metadata, _ = create_tournament_from_gotha(
        conn, FRENCH_XML_PATH, "mcmahon"
    )

    assert imported_metadata["mm_bar"] == 3
    assert imported_metadata["mm_floor"] == -20
    assert imported_metadata["mm_zero"] == 30

    participant_rows = list_tournament_participants(conn, tournament_id)
    assert len(participant_rows) == len(imported_metadata["players"])
    assert [row["initial_score"] for row in participant_rows[:4]] == [33.0, 32.0, 31.0, 30.0]

    standings = get_tournament_standings(conn, tournament_id)
    assert sorted(row["mms"] for row in standings) == list(range(15, 34))


def test_pending_participants_expose_null_public_id_and_stable_internal_id():
    conn = create_db()
    conn.execute(
        "INSERT INTO tournaments (name, rounds, pairing_system, tournament_type) VALUES (?, ?, ?, ?)",
        ("Pending tournament", 2, "swiss", "swiss"),
    )
    tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO tournament_pending_players (tournament_id, display_name, suggested_name, rating, rank, category, source_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tournament_id, "Pending Player", None, 1500.0, 1, "", "pending-player"),
    )
    conn.commit()

    rows = list_tournament_participants(conn, tournament_id)

    assert len(rows) == 1
    assert rows[0]["is_pending"] is True
    assert rows[0]["id"] is None
    assert rows[0]["player_id"] < 0
    assert rows[0]["display_name"] == "Pending Player"


def test_materialize_pending_players_uses_resolved_player_id_when_set():
    conn = create_db()
    conn.execute(
        "INSERT INTO players (id, first_name, last_name, display_name, rating, active) VALUES (?, ?, ?, ?, ?, ?)",
        (1, "Juan Felipe", "Burgos", "Juan Felipe Burgos", 1500, 1),
    )
    conn.execute(
        "INSERT INTO tournaments (name, rounds, pairing_system, tournament_type) VALUES (?, ?, ?, ?)",
        ("Pending resolution", 2, "swiss", "swiss"),
    )
    tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO tournament_pending_players (tournament_id, display_name, suggested_name, resolved_player_id, rating, rank, category, source_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (tournament_id, "Burgos Juan", "Juan Felipe Burgos", 1, 1500.0, 1, "", "burgosjuan"),
    )
    conn.commit()

    created = _materialize_pending_players(conn, tournament_id)

    assert created == 1
    assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 1
    assert conn.execute("SELECT player_id FROM tournament_participants WHERE tournament_id = ?", (tournament_id,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM tournament_pending_players WHERE tournament_id = ?", (tournament_id,)).fetchone()[0] == 0


def test_pending_resolution_updates_canonical_display_name_before_materialization():
    conn = create_db()
    conn.execute(
        "INSERT INTO players (id, first_name, last_name, display_name, rating, active) VALUES (?, ?, ?, ?, ?, ?)",
        (1, "Juan Felipe", "Burgos", "Juan Felipe Burgos", 1500, 1),
    )
    conn.execute(
        "INSERT INTO tournaments (name, rounds, pairing_system, tournament_type) VALUES (?, ?, ?, ?)",
        ("Pending canonical name", 2, "swiss", "swiss"),
    )
    tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    pending_id = conn.execute(
        "INSERT INTO tournament_pending_players (tournament_id, display_name, suggested_name, rating, rank, category, source_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tournament_id, "Burgos Juan", "Juan Felipe Burgos", 1500.0, 1, "", "burgosjuan"),
    ).lastrowid
    conn.commit()

    _materialize_pending_players(conn, tournament_id, pending_id=pending_id)

    pending_row = conn.execute(
        "SELECT display_name FROM tournament_pending_players WHERE id = ?",
        (pending_id,),
    ).fetchone()
    assert pending_row is None

    participant_display_name = conn.execute(
        "SELECT p.display_name FROM tournament_participants tp JOIN players p ON p.id = tp.player_id WHERE tp.tournament_id = ?",
        (tournament_id,),
    ).fetchone()[0]
    assert participant_display_name == "Juan Felipe Burgos"


def test_export_tournament_results_returns_opengotha_xml():
    conn = create_db()
    conn.execute(
        "INSERT INTO tournaments (name, rounds, pairing_system, tournament_type) VALUES (?, ?, ?, ?)",
        ("Torneio São Paulo", 1, "swiss", "swiss"),
    )
    tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.executemany(
        "INSERT INTO players (id, first_name, last_name, display_name, rating, active) VALUES (?, ?, ?, ?, ?, 1)",
        [
            (1, "João", "Silva", "João Silva", 1500),
            (2, "Ana", "Müller", "Ana Müller", 1450),
        ],
    )
    conn.execute(
        "INSERT INTO tournament_rounds (id, tournament_id, round_number, status) VALUES (?, ?, ?, 'completed')",
        (1, tournament_id, 1),
    )
    conn.execute(
        """
        INSERT INTO tournament_pairings
            (round_id, board_number, white_player_id, black_player_id, result, is_bye)
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (1, 1, 1, 2, "1-0"),
    )
    conn.commit()

    exported = export_tournament_results(conn, tournament_id)

    assert exported.startswith("\ufeff")
    assert exported.encode("utf-8").startswith(b"\xef\xbb\xbf")
    root = ET.fromstring(exported.lstrip("\ufeff"))
    assert root.tag == "Tournament"
    players = root.findall("./Players/Player")
    assert {player.get("firstName") for player in players} == {"João", "Ana"}
    game = root.find("./Games/Game")
    assert game is not None
    assert game.get("result") == "RESULT_WHITEWINS"
    white = next(player for player in players if player.get("firstName") == "João")
    black = next(player for player in players if player.get("firstName") == "Ana")
    assert game.get("whitePlayer") == normalize_key(white.get("name") + white.get("firstName"))
    assert game.get("blackPlayer") == normalize_key(black.get("name") + black.get("firstName"))


def test_pairing_scenario_generator_uses_current_tournament_logic():
    conn = create_db()
    conn.execute("ALTER TABLE tournaments ADD COLUMN mm_bar INTEGER DEFAULT 8")
    conn.execute("ALTER TABLE tournaments ADD COLUMN mm_floor INTEGER DEFAULT -30")
    conn.execute("ALTER TABLE tournaments ADD COLUMN mm_zero INTEGER DEFAULT 0")
    conn.execute(
        "ALTER TABLE tournament_participants ADD COLUMN mc_seeds_calculated INTEGER NOT NULL DEFAULT 0"
    )
    conn.executemany(
        "INSERT INTO players (id, first_name, last_name, display_name, rating, active) VALUES (?, ?, ?, ?, ?, 1)",
        [
            (player_id, f"Player{player_id}", "Test", f"Player{player_id} Test", 2200 - player_id * 10)
            for player_id in range(1, max(PLAYER_COUNTS) + 1)
        ],
    )
    conn.commit()

    rng = random.Random(SEED)
    scenarios = []
    for tournament_type in ("swiss", "mcmahon"):
        for index, count in enumerate(PLAYER_COUNTS, 1):
            scenario = create_pairing_test_tournament(
                conn, tournament_type, count, index, rng
            )
            scenarios.append((scenario[0], tournament_type, count, scenario[1], scenario[2]))

    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0

    for tournament_id, tournament_type, count, rounds_created, results_created in scenarios:
        assert rounds_created == ROUNDS
        assert results_created > 0

        participant_ids = {
            row["player_id"]
            for row in conn.execute(
                "SELECT player_id FROM tournament_participants WHERE tournament_id = ?",
                (tournament_id,),
            ).fetchall()
        }
        assert len(participant_ids) == count

        rounds = conn.execute(
            "SELECT id FROM tournament_rounds WHERE tournament_id = ? ORDER BY round_number",
            (tournament_id,),
        ).fetchall()
        assert len(rounds) == ROUNDS
        for round_row in rounds:
            pairings = conn.execute(
                "SELECT white_player_id, black_player_id, is_bye FROM tournament_pairings WHERE round_id = ?",
                (round_row["id"],),
            ).fetchall()
            played_ids = {
                player_id
                for pairing in pairings
                for player_id in (pairing["white_player_id"], pairing["black_player_id"])
                if player_id is not None
            }
            assert played_ids == participant_ids
            assert sum(pairing["is_bye"] for pairing in pairings) == count % 2

        if tournament_type == "mcmahon":
            scores = conn.execute(
                "SELECT seed_rank, initial_score FROM tournament_participants WHERE tournament_id = ? ORDER BY seed_rank",
                (tournament_id,),
            ).fetchall()
            assert [row["initial_score"] for row in scores] == [
                    float(8 - rank + 1 + count) for rank in range(1, count + 1)
            ]


def test_mcmahon_seed_recalculation_uses_tournament_metadata_and_is_guarded():
    conn = create_db()
    conn.execute(
        "ALTER TABLE tournaments ADD COLUMN mm_bar INTEGER DEFAULT 8"
    )
    conn.execute(
        "ALTER TABLE tournaments ADD COLUMN mm_floor INTEGER DEFAULT -30"
    )
    conn.execute(
        "ALTER TABLE tournaments ADD COLUMN mm_zero INTEGER DEFAULT 30"
    )
    conn.execute(
        "ALTER TABLE tournament_participants ADD COLUMN mc_seeds_calculated INTEGER NOT NULL DEFAULT 0"
    )
    conn.execute(
        "INSERT INTO players (id, first_name, last_name, display_name, rating, active) VALUES (?, ?, ?, ?, ?, 1)",
        (1, "Alice", "Alpha", "Alpha, Alice", 1500.0),
    )
    conn.execute(
        "INSERT INTO players (id, first_name, last_name, display_name, rating, active) VALUES (?, ?, ?, ?, ?, 1)",
        (2, "Bob", "Bravo", "Bravo, Bob", 1450.0),
    )
    conn.execute(
        "INSERT INTO tournaments (name, rounds, pairing_system, tournament_type, mm_bar, mm_floor, mm_zero) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Custom McMahon", 3, "mcmahon", "mcmahon", 5, -10, 10),
    )
    tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    add_participant(conn, tournament_id, 1)
    add_participant(conn, tournament_id, 2)

    rows = conn.execute(
        "SELECT seed_rank, initial_score FROM tournament_participants WHERE tournament_id = ? ORDER BY seed_rank",
        (tournament_id,),
    ).fetchall()
    assert [(row["seed_rank"], row["initial_score"]) for row in rows] == [
        (1, 15.0),
        (2, 14.0),
    ]

    conn.execute(
        "UPDATE tournament_participants SET seed_rank = 99, initial_score = -999, mc_seeds_calculated = 1 WHERE tournament_id = ?",
        (tournament_id,),
    )
    _recalculate_mcmahon_seeds(conn, tournament_id)

    persisted = conn.execute(
        "SELECT seed_rank, initial_score FROM tournament_participants WHERE tournament_id = ? ORDER BY seed_rank",
        (tournament_id,),
    ).fetchall()
    assert [(row["seed_rank"], row["initial_score"]) for row in persisted] == [
        (99, -999.0),
        (99, -999.0),
    ]


def test_tournament_rounds_are_clamped_to_a_minimum_of_one():
    assert normalize_tournament_rounds(0) == 1
    assert normalize_tournament_rounds(-2) == 1
    assert normalize_tournament_rounds(3) == 3


def test_completion_refresh_skips_already_completed_rounds(monkeypatch):
    import services.tournament_service as tournament_service

    conn = create_db()
    conn.execute(
        "INSERT INTO tournaments (name, rounds, pairing_system, tournament_type) VALUES (?, ?, ?, ?)",
        ("Completed rounds", 3, "swiss", "swiss"),
    )
    tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.executemany(
        "INSERT INTO tournament_rounds (id, tournament_id, round_number, status) VALUES (?, ?, ?, 'completed')",
        [(round_id, tournament_id, round_id) for round_id in range(1, 4)],
    )
    conn.commit()

    calls = []

    def unexpected_round_query(connection, round_id):
        calls.append(round_id)
        raise AssertionError("completed rounds should not be re-counted")

    monkeypatch.setattr(tournament_service, "_round_is_complete", unexpected_round_query)

    _refresh_tournament_completion_state(conn, tournament_id, round_id=3)

    assert calls == []
    assert conn.execute(
        "SELECT status FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()[0] == "completed"


def test_manual_mcmahon_participants_are_ranked_by_strength_not_insertion_order():
    conn = create_db()
    conn.execute(
        "INSERT INTO players (id, first_name, last_name, display_name, rating, active) VALUES (?, ?, ?, ?, ?, 1)",
        (1, "Leonardo", "Bravo", "Bravo, Leonardo", 1844.8),
    )
    conn.execute(
        "INSERT INTO players (id, first_name, last_name, display_name, rating, active) VALUES (?, ?, ?, ?, ?, 1)",
        (2, "Farlan", "Andrade", "Andrade, Farlan", 2080.0),
    )
    conn.execute(
        "INSERT INTO tournaments (name, rounds, pairing_system, tournament_type) VALUES (?, ?, ?, ?)",
        ("McMahon strength seeding", 3, "mcmahon", "mcmahon"),
    )
    tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    add_participant(conn, tournament_id, 1)
    add_participant(conn, tournament_id, 2)

    rows = conn.execute(
        "SELECT player_id, initial_score FROM tournament_participants WHERE tournament_id = ? ORDER BY seed_rank",
        (tournament_id,),
    ).fetchall()

    assert [(row["player_id"], row["initial_score"]) for row in rows] == [
        (2, 8.0),
        (1, 7.0),
    ]


def test_participant_list_can_be_managed_independently_of_tournament_creation():
    conn = create_db()
    conn.execute(
        "INSERT INTO tournaments (name, rounds, pairing_system) VALUES (?, ?, ?)",
        ("Empty event", 3, "swiss"),
    )
    tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    seed_players(conn)

    assert conn.execute(
        "SELECT COUNT(*) FROM tournament_participants WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone()[0] == 0
    add_participant(conn, tournament_id, 1)
    add_participant(conn, tournament_id, 2)
    assert conn.execute(
        "SELECT COUNT(*) FROM tournament_participants WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone()[0] == 2
    remove_participant(conn, tournament_id, 2)
    assert conn.execute(
        "SELECT COUNT(*) FROM tournament_participants WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone()[0] == 1


def test_odd_roster_persists_a_bye():
    conn = create_db()
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=5, pairing_system="swiss")
    for player_id in range(1, 6):
        add_participant(conn, tournament_id, player_id)

    _, pairings = generate_next_round(conn, tournament_id)

    assert len(pairings) == 3
    assert sum(pairing["is_bye"] for pairing in pairings) == 1
    bye = conn.execute(
        "SELECT white_player_id, black_player_id, is_bye FROM tournament_pairings WHERE is_bye = 1"
    ).fetchone()
    assert bye["black_player_id"] is None
    assert bye["is_bye"] == 1
    assert conn.execute(
        "SELECT received_bye FROM tournament_participants WHERE player_id = ?",
        (bye["white_player_id"],),
    ).fetchone()[0] == 1


def test_set_round_player_status_rejects_players_already_paired_in_round():
    conn = create_db()
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=5, pairing_system="swiss")
    for player_id in range(1, 11):
        add_participant(conn, tournament_id, player_id)
    round_id, _ = generate_next_round(conn, tournament_id)

    pairing = conn.execute(
        "SELECT white_player_id, black_player_id FROM tournament_pairings WHERE round_id = ? AND is_bye = 0 LIMIT 1",
        (round_id,),
    ).fetchone()

    try:
        set_round_player_status(conn, tournament_id, round_id, pairing["white_player_id"], "absent")
    except ValueError as exc:
        assert "already paired" in str(exc).lower()
    else:
        raise AssertionError("Players already paired in a round should not be marked absent or bye")


def test_manual_pair_and_selected_pairing_ignore_players_already_marked_for_the_round():
    conn = create_db()
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=5, pairing_system="swiss")
    for player_id in range(1, 11):
        add_participant(conn, tournament_id, player_id)
    round_id, _ = generate_next_round(conn, tournament_id)

    conn.execute("DELETE FROM tournament_pairings WHERE round_id = ?", (round_id,))
    conn.execute("DELETE FROM tournament_round_players WHERE round_id = ?", (round_id,))
    conn.commit()
    set_round_player_status(conn, tournament_id, round_id, 1, "absent")

    try:
        manual_pair(conn, tournament_id, round_id, 1, 2)
    except ValueError as exc:
        assert "already marked" in str(exc).lower()
    else:
        raise AssertionError("A player already marked absent/bye in a round must not be paired manually")

    pair_selected_players(conn, tournament_id, round_id, [1, 2, 3])

    paired = conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ? AND (white_player_id = 1 OR black_player_id = 1)",
        (round_id,),
    ).fetchone()[0]
    assert paired == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ?",
        (round_id,),
    ).fetchone()[0] >= 1


def test_manual_pair_and_unpair_respect_round_occupancy():
    conn = create_db()
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=5, pairing_system="swiss")
    for player_id in range(1, 11):
        add_participant(conn, tournament_id, player_id)
    round_id, _ = generate_next_round(conn, tournament_id)
    pairing = conn.execute(
        "SELECT id, board_number, white_player_id, black_player_id FROM tournament_pairings WHERE round_id = ? AND is_bye = 0 LIMIT 1",
        (round_id,),
    ).fetchone()

    unpair(conn, tournament_id, pairing["id"])
    assert conn.execute(
        "SELECT COUNT(*) FROM tournament_round_players WHERE round_id = ? AND player_id IN (?, ?)",
        (round_id, pairing["white_player_id"], pairing["black_player_id"]),
    ).fetchone()[0] == 0
    manual_pair(conn, tournament_id, round_id, pairing["white_player_id"], pairing["black_player_id"])
    repaired = conn.execute(
        "SELECT board_number FROM tournament_pairings WHERE round_id = ? AND white_player_id = ? AND black_player_id = ?",
        (round_id, pairing["white_player_id"], pairing["black_player_id"]),
    ).fetchone()
    assert repaired["board_number"] == pairing["board_number"]


def test_delete_tournament_removes_all_dependent_records():
    conn = create_db()
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=5, pairing_system="swiss")
    conn.execute(
        """
        INSERT INTO tournament_pending_players
            (tournament_id, display_name, rating, rank, category)
        VALUES (?, ?, ?, ?, ?)
        """,
        (tournament_id, "Pending Player", 1500, 1, ""),
    )
    for player_id in range(1, 11):
        add_participant(conn, tournament_id, player_id)
    generate_next_round(conn, tournament_id)

    delete_tournament(conn, tournament_id)

    for table in (
        "tournaments",
        "tournament_participants",
        "tournament_rounds",
        "tournament_round_players",
        "tournament_pairings",
        "tournament_pending_players",
    ):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_delete_missing_tournament_is_rejected():
    conn = create_db()

    try:
        delete_tournament(conn, 999)
    except ValueError as exc:
        assert str(exc) == "Tournament not found"
    else:
        raise AssertionError("Expected missing tournament deletion to fail")


def test_manual_bye_and_absence_are_recorded_for_a_round():
    conn = create_db()
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=5, pairing_system="swiss")
    for player_id in range(1, 11):
        add_participant(conn, tournament_id, player_id)
    round_id, _ = generate_next_round(conn, tournament_id)
    unpaired = conn.execute(
        "SELECT player_id FROM tournament_participants WHERE tournament_id = ? AND player_id NOT IN (SELECT player_id FROM tournament_round_players WHERE round_id = ?)",
        (tournament_id, round_id),
    ).fetchall()
    assert len(unpaired) == 0

    # Make a fresh round with an even roster and manually change two statuses.
    conn.execute("DELETE FROM tournament_pairings")
    conn.execute("DELETE FROM tournament_round_players")
    conn.execute("DELETE FROM tournament_rounds")
    conn.commit()
    conn.execute("UPDATE tournaments SET rounds = 2 WHERE id = ?", (tournament_id,))
    conn.commit()
    round_id, _ = generate_next_round(conn, tournament_id)
    conn.execute("DELETE FROM tournament_pairings WHERE round_id = ?", (round_id,))
    conn.execute("DELETE FROM tournament_round_players WHERE round_id = ?", (round_id,))
    conn.commit()
    set_round_player_status(conn, tournament_id, round_id, 1, "bye")
    set_round_player_status(conn, tournament_id, round_id, 2, "absent")

    statuses = dict(conn.execute(
        "SELECT player_id, status FROM tournament_round_players WHERE round_id = ?",
        (round_id,),
    ).fetchall())
    assert statuses == {1: "bye", 2: "absent"}


def test_selected_pairing_turns_one_player_into_bye_and_pairs_multiple_players():
    conn = create_db()
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=5, pairing_system="swiss")
    for player_id in range(1, 11):
        add_participant(conn, tournament_id, player_id)
    round_id, _ = generate_next_round(conn, tournament_id)
    conn.execute("DELETE FROM tournament_pairings WHERE round_id = ?", (round_id,))
    conn.execute("DELETE FROM tournament_round_players WHERE round_id = ?", (round_id,))
    conn.commit()

    pair_selected_players(conn, tournament_id, round_id, [1, 2, 3])

    pair_count = conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ? AND is_bye = 0",
        (round_id,),
    ).fetchone()[0]
    bye_count = conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ? AND is_bye = 1",
        (round_id,),
    ).fetchone()[0]
    assert pair_count == 1
    assert bye_count == 1


def test_empty_selected_pairing_pairs_remaining_unpaired_players():
    conn = create_db()
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=5, pairing_system="swiss")
    for player_id in range(1, 11):
        add_participant(conn, tournament_id, player_id)
    round_id, _ = generate_next_round(conn, tournament_id)
    conn.execute("DELETE FROM tournament_pairings WHERE round_id = ?", (round_id,))
    conn.execute("DELETE FROM tournament_round_players WHERE round_id = ?", (round_id,))
    conn.commit()

    pair_selected_players(conn, tournament_id, round_id, [])

    remaining = conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ?",
        (round_id,),
    ).fetchone()[0]
    assert remaining >= 1

    bye_count = conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ? AND is_bye = 1",
        (round_id,),
    ).fetchone()[0]
    assert bye_count in (0, 1)


def test_empty_selected_pairing_avoids_avoidable_repeat_pairings():
    conn = create_db()
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=5, pairing_system="swiss")
    for player_id in range(1, 5):
        add_participant(conn, tournament_id, player_id)

    round1_id, _ = generate_next_round(conn, tournament_id)
    conn.execute("DELETE FROM tournament_pairings WHERE round_id = ?", (round1_id,))
    conn.execute("DELETE FROM tournament_round_players WHERE round_id = ?", (round1_id,))
    conn.commit()
    manual_pair(conn, tournament_id, round1_id, 1, 2)
    manual_pair(conn, tournament_id, round1_id, 3, 4)

    round2_id, _ = generate_next_round(conn, tournament_id)
    conn.execute("DELETE FROM tournament_pairings WHERE round_id = ?", (round2_id,))
    conn.execute("DELETE FROM tournament_round_players WHERE round_id = ?", (round2_id,))
    conn.commit()

    pair_selected_players(conn, tournament_id, round2_id, [])

    round2_pairs = {
        frozenset((row["white_player_id"], row["black_player_id"]))
        for row in conn.execute(
            "SELECT white_player_id, black_player_id FROM tournament_pairings WHERE round_id = ? AND is_bye = 0",
            (round2_id,),
        ).fetchall()
    }
    assert frozenset((1, 2)) not in round2_pairs
    assert frozenset((3, 4)) not in round2_pairs


def test_process_tournament_round_matches_inserts_completed_pairings_into_rating_table():
    conn = create_db()
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=5, pairing_system="swiss")
    for player_id in range(1, 11):
        add_participant(conn, tournament_id, player_id)
    round_id, _ = generate_next_round(conn, tournament_id)

    pairing_ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM tournament_pairings WHERE round_id = ? AND is_bye = 0 ORDER BY id",
            (round_id,),
        ).fetchall()
    ]
    conn.execute(
        "UPDATE tournament_pairings SET result = '1-0' WHERE id = ?",
        (pairing_ids[0],),
    )
    conn.execute(
        "UPDATE tournament_pairings SET result = '0-1' WHERE id = ?",
        (pairing_ids[1],),
    )
    conn.commit()

    inserted = process_tournament_round_matches(
        conn,
        tournament_id,
        round_id=round_id,
        match_date="2026-07-01",
        event="Tournament round import",
    )

    assert inserted == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM matches WHERE match_date = '2026-07-01' AND event = 'Tournament round import'"
    ).fetchone()[0] == 2

    round_number = conn.execute(
        "SELECT round_number FROM tournament_rounds WHERE id = ?",
        (round_id,),
    ).fetchone()[0]
    matches = conn.execute(
        "SELECT notes, round_number FROM matches WHERE event = 'Tournament round import' ORDER BY id"
    ).fetchall()
    assert all(row["notes"] == str(round_number) for row in matches)
    assert all(row["round_number"] == round_number for row in matches)


def test_completed_pairing_and_materialized_match_stay_in_sync():
    conn = create_db()
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=1, pairing_system="swiss")
    conn.execute(
        "UPDATE tournaments SET status = 'completed', begin_date = '2026-08-20' WHERE id = ?",
        (tournament_id,),
    )
    for player_id in (1, 2, 3):
        add_participant(conn, tournament_id, player_id)
    conn.execute(
        "INSERT INTO tournament_rounds (id, tournament_id, round_number, status) VALUES (1, ?, 1, 'completed')",
        (tournament_id,),
    )
    conn.execute(
        "INSERT INTO tournament_pairings (id, round_id, board_number, white_player_id, black_player_id, result) VALUES (10, 1, 1, 1, 2, '1-0')"
    )
    conn.commit()

    set_pairing_result(conn, tournament_id, 10, "1/2-1/2")
    match = conn.execute(
        "SELECT match_date, event, white_player_id, black_player_id, result FROM matches WHERE tournament_pairing_id = 10"
    ).fetchone()
    assert tuple(match) == ("2026-08-20", "Manual tournament", 1, 2, "1/2-1/2")

    update_pairing(conn, tournament_id, 10, 2, 3)
    match = conn.execute(
        "SELECT white_player_id, black_player_id FROM matches WHERE tournament_pairing_id = 10"
    ).fetchone()
    assert tuple(match) == (2, 3)

    sync_tournament_matches(conn, tournament_id, name="Completed Open", match_date="2026-08-21")
    match = conn.execute(
        "SELECT match_date, event FROM matches WHERE tournament_pairing_id = 10"
    ).fetchone()
    assert tuple(match) == ("2026-08-21", "Completed Open")

    sync_match_pairing(conn, match_id=1, white_player_id=3, black_player_id=2, result="0-1", handicap_stones=0)
    pairing = conn.execute(
        "SELECT white_player_id, black_player_id, result FROM tournament_pairings WHERE id = 10"
    ).fetchone()
    assert tuple(pairing) == (3, 2, "0-1")

    set_pairing_result(conn, tournament_id, 10, "")
    assert conn.execute("SELECT 1 FROM matches WHERE tournament_pairing_id = 10").fetchone() is None


def test_save_tournament_matches_materializes_all_completed_pairings():
    conn = create_db()
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=2, pairing_system="swiss")
    conn.execute(
        "UPDATE tournaments SET begin_date = '2026-08-20' WHERE id = ?",
        (tournament_id,),
    )
    for player_id in (1, 2, 3, 4):
        add_participant(conn, tournament_id, player_id)
    conn.executemany(
        "INSERT INTO tournament_rounds (id, tournament_id, round_number, status) VALUES (?, ?, ?, 'completed')",
        [(1, tournament_id, 1), (2, tournament_id, 2)],
    )
    conn.executemany(
        """
        INSERT INTO tournament_pairings
            (id, round_id, board_number, white_player_id, black_player_id, result)
        VALUES (?, ?, 1, ?, ?, '1-0')
        """,
        [(10, 1, 1, 2), (20, 2, 3, 4)],
    )
    conn.commit()

    assert save_tournament_matches(conn, tournament_id) == 2
    matches = conn.execute(
        "SELECT tournament_pairing_id, match_date, event FROM matches ORDER BY tournament_pairing_id"
    ).fetchall()
    assert [tuple(row) for row in matches] == [
        (10, "2026-08-20", "Manual tournament"),
        (20, "2026-08-20", "Manual tournament"),
    ]


def test_set_pairing_result_rejects_invalid_and_bye_outcomes():
    conn = create_db()
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=5, pairing_system="swiss")
    for player_id in range(1, 11):
        add_participant(conn, tournament_id, player_id)
    round_id, _ = generate_next_round(conn, tournament_id)
    conn.execute(
        "INSERT INTO tournament_pairings (round_id, board_number, white_player_id, black_player_id, is_bye) VALUES (?, (SELECT COALESCE(MAX(board_number), 0) + 1 FROM tournament_pairings WHERE round_id = ?), ?, NULL, 1)",
        (round_id, round_id, 1),
    )
    conn.commit()
    bye_pairing = conn.execute(
        "SELECT id FROM tournament_pairings WHERE round_id = ? AND is_bye = 1 LIMIT 1",
        (round_id,),
    ).fetchone()

    try:
        set_pairing_result(conn, tournament_id, bye_pairing["id"], "1-0")
    except ValueError:
        pass
    else:
        raise AssertionError("Bye pairings should reject result updates")

    valid_pairing = conn.execute(
        "SELECT id FROM tournament_pairings WHERE round_id = ? AND is_bye = 0 LIMIT 1",
        (round_id,),
    ).fetchone()
    try:
        set_pairing_result(conn, tournament_id, valid_pairing["id"], "invalid")
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid results should be rejected")


def test_tournament_marks_completed_after_final_round_results():
    conn = create_db()
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=1, pairing_system="swiss")
    for player_id in range(1, 11):
        add_participant(conn, tournament_id, player_id)
    round_id, _ = generate_next_round(conn, tournament_id)

    pairing_ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM tournament_pairings WHERE round_id = ? AND is_bye = 0 ORDER BY id",
            (round_id,),
        ).fetchall()
    ]
    for pairing_id in pairing_ids:
        set_pairing_result(conn, tournament_id, pairing_id, "1-0")

    conn.execute(
        "UPDATE tournaments SET status = 'active' WHERE id = ?",
        (tournament_id,),
    )
    conn.commit()

    status = conn.execute("SELECT status FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()[0]
    assert status == "active"

    conn.execute(
        "UPDATE tournament_rounds SET status = 'completed' WHERE id = ?",
        (round_id,),
    )
    conn.execute(
        "UPDATE tournaments SET status = 'completed' WHERE id = ?",
        (tournament_id,),
    )
    conn.commit()
    assert conn.execute("SELECT status FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()[0] == "completed"


def test_export_tournament_results_generates_opengotha_games():
    conn = create_db()
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=3, pairing_system="swiss")
    for player_id in range(1, 11):
        add_participant(conn, tournament_id, player_id)

    round_id, _ = generate_next_round(conn, tournament_id)

    pairing_id = conn.execute(
        "SELECT id FROM tournament_pairings WHERE round_id = ? AND is_bye = 0 LIMIT 1",
        (round_id,),
    ).fetchone()[0]
    set_pairing_result(conn, tournament_id, pairing_id, "1-0")

    xml_text = export_tournament_results(conn, tournament_id)
    root = ET.fromstring(xml_text.lstrip("\ufeff"))
    games = root.findall("./Games/Game")

    assert len(games) == 5
    assert any(game.get("result") == "RESULT_WHITEWINS" for game in games)
    assert root.find("./TournamentParameterSet/GeneralParameterSet").get("numberOfRounds") == "3"
