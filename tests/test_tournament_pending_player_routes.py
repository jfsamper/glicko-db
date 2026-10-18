import sqlite3

import routes.admin as admin_routes


def _create_tournament(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tournament_id = conn.execute(
            "INSERT INTO tournaments (name, rounds) VALUES (?, 1)",
            ("Pending player test",),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO players (first_name, last_name, slug)
            VALUES (?, ?, ?)
            """,
            ("Sebastian", "Bravo", "sebastian-bravo-test"),
        )
        conn.commit()
        category_config = admin_routes.get_category_config(conn)
        return tournament_id, str(round(category_config["glicko_m"]))
    finally:
        conn.close()


def test_similar_pending_player_can_be_created_after_confirmation(
        admin_client, isolate_test_database, monkeypatch):
    tournament_id, glicko = _create_tournament(isolate_test_database)
    monkeypatch.setattr(
        admin_routes,
        "_suggest_player_name",
        lambda _name, _conn: "Bravo, Sebastian",
    )
    form_data = {
        "first_name": "Sebastian",
        "last_name": "Rios",
        "glicko": glicko,
    }

    warning = admin_client.post(
        f"/admin/tournaments/{tournament_id}/players/create?lang=en",
        data=form_data,
    )

    assert warning.status_code == 200
    assert b"Did you mean Bravo, Sebastian?" in warning.data
    assert b'name="allow_similar" value="1"' in warning.data
    assert b'name="first_name" value="Sebastian"' in warning.data

    conn = sqlite3.connect(isolate_test_database)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM tournament_pending_players WHERE tournament_id = ?",
            (tournament_id,),
        ).fetchone()[0] == 0
    finally:
        conn.close()

    created = admin_client.post(
        f"/admin/tournaments/{tournament_id}/players/create?lang=en",
        data={**form_data, "allow_similar": "1"},
    )

    assert created.status_code == 302
    conn = sqlite3.connect(isolate_test_database)
    try:
        pending_player = conn.execute(
            """
            SELECT first_name, last_name
            FROM tournament_pending_players
            WHERE tournament_id = ?
            """,
            (tournament_id,),
        ).fetchone()
        assert pending_player == ("Sebastian", "Rios")
    finally:
        conn.close()


def test_exact_existing_player_still_cannot_be_added_as_pending(
        admin_client, isolate_test_database):
    tournament_id, glicko = _create_tournament(isolate_test_database)

    response = admin_client.post(
        f"/admin/tournaments/{tournament_id}/players/create?lang=en",
        data={
            "first_name": "Sebastian",
            "last_name": "Bravo",
            "glicko": glicko,
            "allow_similar": "1",
        },
    )

    assert response.status_code == 302
    conn = sqlite3.connect(isolate_test_database)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM tournament_pending_players WHERE tournament_id = ?",
            (tournament_id,),
        ).fetchone()[0] == 0
    finally:
        conn.close()
