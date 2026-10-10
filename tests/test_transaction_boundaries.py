import sqlite3

import pytest

import services.tournament_pairing as tournament_pairing
from services.tournament_pairing import pair_selected_players, unpair_all
from services.transactions import command, commit, transactional
from test_tournament_service import (
    add_participant,
    create_db,
    create_manual_tournament,
    generate_next_round,
    seed_players,
)


def _pairing_count(conn, round_id):
    return conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ?", (round_id,)
    ).fetchone()[0]


def _round_with_three_pairings(conn):
    seed_players(conn)
    tournament_id = create_manual_tournament(conn, rounds=1, pairing_system="swiss")
    for player_id in range(1, 6):
        add_participant(conn, tournament_id, player_id)
    round_id, _ = generate_next_round(conn, tournament_id)
    assert _pairing_count(conn, round_id) == 3
    return tournament_id, round_id


def test_unpair_all_is_all_or_nothing(monkeypatch):
    conn = create_db()
    tournament_id, round_id = _round_with_three_pairings(conn)
    original_unpair = tournament_pairing.unpair
    calls = []

    def unpair_that_fails_second_time(conn_, tournament_id_, pairing_id):
        calls.append(pairing_id)
        if len(calls) == 2:
            raise RuntimeError("simulated failure")
        return original_unpair(conn_, tournament_id_, pairing_id)

    monkeypatch.setattr(tournament_pairing, "unpair", unpair_that_fails_second_time)

    with pytest.raises(RuntimeError):
        unpair_all(conn, tournament_id, round_id)

    assert _pairing_count(conn, round_id) == 3


def test_pair_selected_players_is_all_or_nothing(monkeypatch):
    conn = create_db()
    tournament_id, round_id = _round_with_three_pairings(conn)
    unpair_all(conn, tournament_id, round_id)
    assert _pairing_count(conn, round_id) == 0
    original_manual_pair = tournament_pairing.manual_pair
    calls = []

    def manual_pair_that_fails_second_time(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise RuntimeError("simulated failure")
        return original_manual_pair(*args, **kwargs)

    monkeypatch.setattr(tournament_pairing, "manual_pair", manual_pair_that_fails_second_time)

    with pytest.raises(RuntimeError):
        pair_selected_players(conn, tournament_id, round_id, [1, 2, 3, 4])

    assert _pairing_count(conn, round_id) == 0


def test_failed_command_rolls_back_writes_from_nested_helpers(tmp_path):
    db_path = tmp_path / "command.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE items (name TEXT)")
    conn.commit()

    @transactional
    def add_then_fail(conn_):
        conn_.execute("INSERT INTO items (name) VALUES ('first')")
        helper_commit(conn_)
        conn_.execute("INSERT INTO items (name) VALUES ('second')")
        raise RuntimeError("boom")

    def helper_commit(conn_):
        commit(conn_)

    with pytest.raises(RuntimeError):
        add_then_fail(conn)
    conn.close()

    check = sqlite3.connect(db_path)
    assert check.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    check.close()


def test_successful_command_commits_once_at_the_end(tmp_path):
    db_path = tmp_path / "command_ok.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE items (name TEXT)")
    conn.commit()

    with command(conn):
        conn.execute("INSERT INTO items (name) VALUES ('a')")
        commit(conn)
        conn.execute("INSERT INTO items (name) VALUES ('b')")
        check = sqlite3.connect(db_path)
        assert check.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
        check.close()

    check = sqlite3.connect(db_path)
    assert check.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
    check.close()
    conn.close()
