import sqlite3

import conftest
import services.rating_service as rating_service
from services.player_service import load_player
from services.tournament_status import _refresh_tournament_completion_state


def _add_tournament(conn, name, status, include_game=False):
    tournament_id = conn.execute(
        "INSERT INTO tournaments (name, rounds, status) VALUES (?, 1, ?)",
        (name, status),
    ).lastrowid
    conn.executemany(
        "INSERT INTO tournament_participants (tournament_id, player_id) VALUES (?, ?)",
        ((tournament_id, 1), (tournament_id, 2)),
    )
    if include_game:
        round_id = conn.execute(
            """
            INSERT INTO tournament_rounds (tournament_id, round_number, status)
            VALUES (?, 1, 'completed')
            """,
            (tournament_id,),
        ).lastrowid
        pairing_id = conn.execute(
            """
            INSERT INTO tournament_pairings
                (round_id, board_number, white_player_id, black_player_id, result, is_bye)
            VALUES (?, 1, 1, 2, '1-0', 0)
            """,
            (round_id,),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO matches
                (match_date, white_player_id, black_player_id, result, event, round_number,
                 tournament_pairing_id)
            VALUES ('2026-08-01', 1, 2, '1-0', ?, 1, ?)
            """,
            (name, pairing_id),
        )
    return tournament_id


def _player_rating(db_path, player_id=1):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT rating FROM players WHERE id = ?", (player_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_only_completed_tournament_games_affect_ratings(isolate_test_database):
    db_path = isolate_test_database
    rating_service.recompute_ratings()
    baseline_rating = _player_rating(db_path)

    conn = sqlite3.connect(db_path)
    try:
        for status in ("active", "draft", "canceled"):
            _add_tournament(conn, f"{status.title()} event", status, include_game=True)
        conn.commit()
    finally:
        conn.close()

    rating_service.recompute_ratings()
    assert _player_rating(db_path) == baseline_rating
    rating_service.mark_dirty("2026-08-01")
    rating_service.update_from_latest_snapshot()
    assert _player_rating(db_path) == baseline_rating

    conn = sqlite3.connect(db_path)
    try:
        _add_tournament(conn, "Completed event", "completed", include_game=True)
        conn.commit()
    finally:
        conn.close()

    rating_service.recompute_ratings()
    assert _player_rating(db_path) != baseline_rating


def test_player_profiles_list_only_completed_tournaments(isolate_test_database):
    conn = sqlite3.connect(isolate_test_database)
    try:
        for status in ("active", "draft", "canceled", "completed"):
            _add_tournament(conn, f"{status.title()} event", status)
        conn.commit()
    finally:
        conn.close()

    profile = load_player(1, lang="en")

    assert [tournament["name"] for tournament in profile["tournaments"]] == [
        "Completed event"
    ]


def test_changing_completed_tournament_status_recomputes_ratings(
    client, isolate_test_database
):
    db_path = isolate_test_database
    rating_service.recompute_ratings()
    baseline_rating = _player_rating(db_path)

    conn = sqlite3.connect(db_path)
    try:
        tournament_id = _add_tournament(
            conn, "Status change event", "completed", include_game=True
        )
        conn.commit()
    finally:
        conn.close()

    rating_service.recompute_ratings()
    assert _player_rating(db_path) != baseline_rating

    conftest.set_admin_session(client, db_path)
    response = client.post(
        f"/admin/tournaments/{tournament_id}/status?lang=en",
        data={"status": "active"},
    )

    assert response.status_code == 302
    assert _player_rating(db_path) == baseline_rating


def test_automatic_completion_recomputes_ratings(isolate_test_database):
    db_path = isolate_test_database
    rating_service.recompute_ratings()
    baseline_rating = _player_rating(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tournament_id = _add_tournament(
            conn, "Auto-completion event", "active", include_game=True
        )
        conn.commit()
        assert _player_rating(db_path) == baseline_rating

        _refresh_tournament_completion_state(conn, tournament_id)
        conn.commit()
    finally:
        conn.close()

    assert _player_rating(db_path) != baseline_rating
