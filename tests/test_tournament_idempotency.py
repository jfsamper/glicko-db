import sqlite3
from pathlib import Path

import pytest

import conftest
import routes.admin as admin_routes
from services.idempotency_service import InvalidIdempotencyKey, claim_command

PROCESS_KEY = "process-round-key-001"


@pytest.fixture
def tournament_db(tmp_path, monkeypatch):
    db_path = tmp_path / "tournament_idempotency.db"
    sqlite3.connect(db_path).close()

    def get_test_db():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    calls = []

    def recording_process(_conn, tournament_id, round_id=None, match_date=None, event=None):
        calls.append((tournament_id, round_id))
        return 0

    monkeypatch.setattr(admin_routes, "get_db", get_test_db)
    monkeypatch.setattr(admin_routes, "process_tournament_round_matches", recording_process)
    return db_path, calls


def _process_round(client, tournament_id, key=None, headers=None):
    data = {"round_id": "20", "match_date": "2026-08-15", "event": "Open"}
    if key is not None:
        data["idempotency_key"] = key
    return client.post(
        f"/admin/tournaments/{tournament_id}/process-round?lang=en",
        data=data,
        headers=headers or {},
    )


def _key_state(db_path, key):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT state FROM command_idempotency_keys WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_repeated_key_replays_outcome_without_running_command_again(client, tournament_db):
    db_path, calls = tournament_db
    conftest.set_admin_session(client, db_path)

    first = _process_round(client, 10, PROCESS_KEY)
    second = _process_round(client, 10, headers={"Idempotency-Key": PROCESS_KEY})

    assert first.status_code == 302
    assert second.status_code == 302
    assert second.headers["Location"] == first.headers["Location"]
    assert calls == [(10, 20)]
    assert _key_state(db_path, PROCESS_KEY) == "completed"


def test_command_without_key_is_refused(client, tournament_db):
    db_path, calls = tournament_db
    conftest.set_admin_session(client, db_path)

    response = _process_round(client, 10)

    assert response.status_code == 302
    assert calls == []


def test_key_reused_for_another_tournament_is_refused(client, tournament_db):
    db_path, calls = tournament_db
    conftest.set_admin_session(client, db_path)

    _process_round(client, 10, PROCESS_KEY)
    response = _process_round(client, 11, PROCESS_KEY)

    assert response.status_code == 302
    assert calls == [(10, 20)]


def test_key_left_pending_by_a_failed_attempt_is_not_run_again(client, tournament_db, monkeypatch):
    db_path, calls = tournament_db
    conftest.set_admin_session(client, db_path)

    def crashing_process(*_args, **_kwargs):
        raise RuntimeError("database went away mid-command")

    monkeypatch.setattr(admin_routes, "process_tournament_round_matches", crashing_process)
    with pytest.raises(RuntimeError):
        _process_round(client, 10, PROCESS_KEY)
    assert _key_state(db_path, PROCESS_KEY) == "in_progress"

    def recording_process(_conn, tournament_id, round_id=None, match_date=None, event=None):
        calls.append((tournament_id, round_id))
        return 0

    monkeypatch.setattr(admin_routes, "process_tournament_round_matches", recording_process)
    response = _process_round(client, 10, PROCESS_KEY)

    assert response.status_code == 302
    assert calls == []


def test_claim_rejects_malformed_keys(tournament_db):
    db_path, _ = tournament_db
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(InvalidIdempotencyKey):
            claim_command(conn, 1, "short", "tournament.round_process", 10)
        with pytest.raises(InvalidIdempotencyKey):
            claim_command(conn, 1, "", "tournament.round_process", 10)
    finally:
        conn.close()


def test_tournament_index_renders_a_key_for_the_import_form(client, isolate_test_database):
    conftest.set_admin_session(client, isolate_test_database)

    response = client.get("/admin/tournaments?lang=en")

    assert response.status_code == 200
    assert b'name="idempotency_key"' in response.data


def test_tournament_command_forms_render_a_fresh_key_per_form():
    repo_root = Path(__file__).resolve().parents[1]
    expected_keys = {
        "templates/admin/tournament.html": 2,
        "templates/admin/tournament_players.html": 4,
        "templates/admin/tournaments.html": 2,
    }
    key_input = 'name="idempotency_key" value="{{ new_idempotency_key() }}"'
    for relative_path, expected in expected_keys.items():
        text = (repo_root / relative_path).read_text(encoding="utf-8")
        assert text.count(key_input) == expected, relative_path


def test_copy_tournament_clones_setup_as_a_fresh_draft(client, isolate_test_database):
    db_path = isolate_test_database
    conftest.set_admin_session(client, db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO tournaments
            (name, short_name, location, description, begin_date, end_date, rounds,
             tournament_type, pairing_system, bye_points, absent_points,
             handicap_enabled, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Original Open", "ORIG", "Bogota", "Annual event",
            "2026-06-01", "2026-06-02", 5, "mcmahon", "mcmahon",
            0.5, 0.0, 1, "completed",
        ),
    )
    source_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """
        INSERT INTO tournament_participants
            (tournament_id, player_id, seed_rating, seed_rank, category,
             initial_score, acceleration, score, received_bye, mc_seeds_calculated)
        VALUES (?, 1, 1800, 2, '2D', 4, 1, 8, 1, 1)
        """,
        (source_id,),
    )
    conn.execute(
        """
        INSERT INTO tournament_pending_players
            (tournament_id, first_name, last_name, rating, rank, category)
        VALUES (?, 'Ana', 'Pending', 1600, 3, '1D')
        """,
        (source_id,),
    )
    conn.commit()
    conn.close()

    response = client.post(
        f"/admin/tournaments/{source_id}/copy?lang=en",
        data={"idempotency_key": "copy-tournament-0001"},
    )

    assert response.status_code == 302
    copied_id = int(response.headers["Location"].rsplit("/", 1)[1].split("?", 1)[0])
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        copied = conn.execute(
            "SELECT * FROM tournaments WHERE id = ?", (copied_id,)
        ).fetchone()
        assert copied["name"] == "Copy of Original Open"
        assert copied["short_name"] is None
        assert copied["location"] == "Bogota"
        assert copied["description"] == "Annual event"
        assert copied["begin_date"] is None
        assert copied["end_date"] is None
        assert copied["rounds"] == 5
        assert copied["pairing_system"] == "mcmahon"
        assert copied["handicap_enabled"] == 1
        assert copied["status"] == "draft"

        participant = conn.execute(
            "SELECT * FROM tournament_participants WHERE tournament_id = ?",
            (copied_id,),
        ).fetchone()
        assert participant["player_id"] == 1
        assert participant["seed_rating"] == 1800
        assert participant["initial_score"] == 4
        assert participant["score"] == 0
        assert participant["received_bye"] == 0
        assert conn.execute(
            """
            SELECT COUNT(*) FROM tournament_pending_players
            WHERE tournament_id = ? AND first_name = 'Ana'
            """,
            (copied_id,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM tournament_rounds WHERE tournament_id = ?",
            (copied_id,),
        ).fetchone()[0] == 0
    finally:
        conn.close()

    replay = client.post(
        f"/admin/tournaments/{source_id}/copy?lang=en",
        data={"idempotency_key": "copy-tournament-0001"},
    )
    assert replay.status_code == 302
    assert replay.headers["Location"] == response.headers["Location"]
