import sqlite3


def _create_tournament(db_path):
    conn = sqlite3.connect(db_path)
    try:
        player_ids = [
            row[0] for row in conn.execute(
                "SELECT id FROM players WHERE active = 1 ORDER BY id DESC LIMIT 2"
            )
        ]
        if len(player_ids) < 2:
            first_id = conn.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM players"
            ).fetchone()[0]
            player_ids = [first_id, first_id + 1]
            conn.executemany(
                """
                INSERT INTO players (id, first_name, last_name, rating, active)
                VALUES (?, ?, ?, 1500, 1)
                """,
                [(player_ids[0], "Batch", "Alpha"), (player_ids[1], "Batch", "Bravo")],
            )
        player_ids.sort()
        conn.execute(
            """
            INSERT INTO tournaments (name, rounds, pairing_system, tournament_type)
            VALUES ('Batch participants', 3, 'swiss', 'swiss')
            """
        )
        tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return tournament_id, player_ids
    finally:
        conn.close()


def test_batch_enroll_participants_and_reject_invalid_batch_atomically(
    admin_client, isolate_test_database
):
    tournament_id, player_ids = _create_tournament(isolate_test_database)
    response = admin_client.get(
        f"/admin/tournaments/{tournament_id}/players?lang=en"
    )
    assert response.status_code == 200
    assert b'name="player_ids"' in response.data
    assert b'name="player_ids"' in response.data
    assert f'value="{player_ids[0]}"'.encode() in response.data

    response = admin_client.post(
        f"/admin/tournaments/{tournament_id}/participants/add-batch?lang=en",
        data={
            "player_ids": [str(player_ids[0]), "999999"],
            "idempotency_key": "batch-enrollment-failure-001",
        },
    )
    assert response.status_code == 302
    conn = sqlite3.connect(isolate_test_database)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM tournament_participants WHERE tournament_id = ?",
            (tournament_id,),
        ).fetchone()[0] == 0
    finally:
        conn.close()

    response = admin_client.post(
        f"/admin/tournaments/{tournament_id}/participants/add-batch?lang=en",
        data={
            "player_ids": [str(player_ids[0]), str(player_ids[1])],
            "idempotency_key": "batch-enrollment-success-001",
        },
    )
    assert response.status_code == 302
    conn = sqlite3.connect(isolate_test_database)
    try:
        enrolled = conn.execute(
            "SELECT player_id FROM tournament_participants WHERE tournament_id = ? ORDER BY player_id",
            (tournament_id,),
        ).fetchall()
        assert enrolled == [(player_ids[0],), (player_ids[1],)]
    finally:
        conn.close()


def test_batch_remove_participants_from_management_page(admin_client, isolate_test_database):
    tournament_id, player_ids = _create_tournament(isolate_test_database)
    conn = sqlite3.connect(isolate_test_database)
    conn.executemany(
        """
        INSERT INTO tournament_participants
            (tournament_id, player_id, seed_rating, seed_rank)
        VALUES (?, ?, 1500, ?)
        """,
        [(tournament_id, player_ids[0], 1), (tournament_id, player_ids[1], 2)],
    )
    conn.commit()
    conn.close()

    response = admin_client.post(
        f"/admin/tournaments/{tournament_id}/participants/remove-batch?lang=en",
        data={
            "player_ids": [str(player_ids[0]), str(player_ids[1])],
            "idempotency_key": "batch-removal-success-001",
        },
    )

    assert response.status_code == 302
    assert f"/admin/tournaments/{tournament_id}/players" in response.headers["Location"]
    conn = sqlite3.connect(isolate_test_database)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM tournament_participants WHERE tournament_id = ?",
            (tournament_id,),
        ).fetchone()[0] == 0
    finally:
        conn.close()
