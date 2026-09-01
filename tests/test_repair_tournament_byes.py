import sqlite3

from scripts.dev_only.repair_tournament_byes import apply_repairs, find_repairs


def create_bye_repair_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE tournaments (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE tournament_participants (
            tournament_id INTEGER, player_id INTEGER, received_bye INTEGER DEFAULT 0
        );
        CREATE TABLE tournament_rounds (
            id INTEGER PRIMARY KEY, tournament_id INTEGER, round_number INTEGER
        );
        CREATE TABLE tournament_pairings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER, board_number INTEGER,
            white_player_id INTEGER, black_player_id INTEGER, is_bye INTEGER DEFAULT 0
        );
        CREATE TABLE tournament_round_players (
            round_id INTEGER, player_id INTEGER, status TEXT,
            UNIQUE(round_id, player_id)
        );
        """
    )
    conn.execute("INSERT INTO tournaments VALUES (1, 'Odd tournament')")
    conn.executemany(
        "INSERT INTO tournament_participants (tournament_id, player_id) VALUES (1, ?)",
        [(1,), (2,), (3,)],
    )
    conn.executemany(
        "INSERT INTO tournament_rounds VALUES (?, 1, ?)",
        [(10, 1), (11, 2), (12, 3)],
    )
    conn.execute(
        "INSERT INTO tournament_pairings (round_id, board_number, white_player_id, black_player_id) VALUES (10, 1, 1, 2)"
    )
    conn.execute(
        "INSERT INTO tournament_pairings (round_id, board_number, white_player_id, black_player_id, is_bye) VALUES (11, 1, 1, NULL, 1)"
    )
    conn.execute(
        "INSERT INTO tournament_pairings (round_id, board_number, white_player_id, black_player_id) VALUES (12, 1, 1, 2)"
    )
    conn.execute(
        "INSERT INTO tournament_round_players VALUES (12, 3, 'absent')"
    )
    return conn


def test_find_repairs_only_missing_bye_with_one_eligible_unpaired_player():
    conn = create_bye_repair_db()

    repairs = find_repairs(conn)

    assert repairs == [
        {
            "tournament_id": 1,
            "tournament_name": "Odd tournament",
            "round_id": 10,
            "round_number": 1,
            "player_id": 3,
        }
    ]
    conn.close()


def test_apply_repairs_creates_bye_pairing_and_round_status():
    conn = create_bye_repair_db()
    repairs = find_repairs(conn)

    apply_repairs(conn, repairs)

    bye = conn.execute(
        "SELECT round_id, board_number, white_player_id, black_player_id, is_bye FROM tournament_pairings WHERE round_id = 10"
    ).fetchall()[-1]
    assert tuple(bye) == (10, 2, 3, None, 1)
    assert conn.execute(
        "SELECT status FROM tournament_round_players WHERE round_id = 10 AND player_id = 3"
    ).fetchone()[0] == "bye"
    assert conn.execute(
        "SELECT received_bye FROM tournament_participants WHERE tournament_id = 1 AND player_id = 3"
    ).fetchone()[0] == 1
    conn.close()