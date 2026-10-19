import conftest
from app import app
import services.common as common


def _create_tournament_with_match(conn, name, status="draft"):
    conn.execute(
        "INSERT INTO tournaments (name, status) VALUES (?, ?)",
        (name, status),
    )
    tournament_id = conn.execute(
        "SELECT last_insert_rowid()"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO tournament_rounds (tournament_id, round_number) VALUES (?, 1)",
        (tournament_id,),
    )
    round_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """
        INSERT INTO tournament_pairings
            (round_id, board_number, white_player_id, black_player_id, result)
        VALUES (?, 1, 1, 2, '1-0')
        """,
        (round_id,),
    )
    pairing_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """
        INSERT INTO matches
            (match_date, white_player_id, black_player_id, result,
             tournament_pairing_id, event)
        VALUES ('2026-09-01', 1, 2, '1-0', ?, ?)
        """,
        (pairing_id, name),
    )
    return tournament_id


def test_admin_can_batch_delete_tournaments_and_related_matches():
    client = app.test_client()
    _, csrf_token = conftest.set_admin_session(client)
    conn = common.get_db()
    first_id = _create_tournament_with_match(conn, "Batch delete alpha")
    second_id = _create_tournament_with_match(conn, "Batch delete beta")
    standalone_match_id = conn.execute(
        """
        INSERT INTO matches
            (match_date, white_player_id, black_player_id, result, event)
        VALUES ('2026-09-02', 1, 2, '0-1', 'Keep standalone match')
        RETURNING id
        """
    ).fetchone()[0]
    conn.commit()
    conn.close()

    response = client.get("/admin/tournaments?lang=en")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="select-all-tournaments"' in body
    assert 'name="tournament_ids"' in body
    assert "Delete selected tournaments" in body

    response = client.post(
        "/admin/tournaments/delete-batch?lang=en",
        data={
            "csrf_token": csrf_token,
            "tournament_ids": [str(first_id), str(second_id)],
        },
    )
    assert response.status_code == 302

    conn = common.get_db()
    assert conn.execute(
        "SELECT COUNT(*) FROM tournaments WHERE id IN (?, ?)",
        (first_id, second_id),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM matches WHERE tournament_pairing_id IS NOT NULL"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM matches WHERE id = ?",
        (standalone_match_id,),
    ).fetchone()[0] == 1
    conn.close()


def test_admin_batch_tournament_delete_rejects_unknown_id_atomically():
    client = app.test_client()
    _, csrf_token = conftest.set_admin_session(client)
    conn = common.get_db()
    tournament_id = _create_tournament_with_match(conn, "Retained tournament")
    conn.commit()
    conn.close()

    response = client.post(
        "/admin/tournaments/delete-batch?lang=en",
        data={
            "csrf_token": csrf_token,
            "tournament_ids": [str(tournament_id), "999999"],
        },
    )
    assert response.status_code == 302

    conn = common.get_db()
    assert conn.execute(
        "SELECT COUNT(*) FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM matches WHERE event = 'Retained tournament'"
    ).fetchone()[0] == 1
    conn.close()
