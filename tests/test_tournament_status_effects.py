import sqlite3

import conftest
import pytest
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


def test_completed_unrated_tournament_games_are_public_but_do_not_change_ratings(
    client, isolate_test_database
):
    db_path = isolate_test_database
    rating_service.recompute_ratings()
    baseline_rating = _player_rating(db_path)

    conn = sqlite3.connect(db_path)
    try:
        tournament_id = _add_tournament(
            conn, "Completed unrated event", "completed", include_game=True
        )
        conn.execute(
            "UPDATE tournaments SET is_rated = 0 WHERE id = ?", (tournament_id,)
        )
        conn.commit()
    finally:
        conn.close()

    rating_service.recompute_ratings()
    assert _player_rating(db_path) == baseline_rating
    rating_service.mark_dirty("2026-08-01")
    rating_service.update_from_latest_snapshot()
    assert _player_rating(db_path) == baseline_rating

    profile = load_player(1, lang="en")
    assert any(
        tournament["name"] == "Completed unrated event"
        and tournament["is_rated_value"] == 0
        for tournament in profile["tournaments"]
    )
    assert any(
        match["event"] == "Completed unrated event" and match["is_unrated"]
        for match in profile["all_matches"]
    )
    public_matches = client.get("/matches?lang=en").get_data(as_text=True)
    assert "Completed unrated event" in public_matches
    assert "Unrated" in public_matches
    public_tournaments = client.get("/tournaments?lang=en").get_data(as_text=True)
    assert "Completed unrated event" in public_tournaments
    assert "Unrated" in public_tournaments


def test_tournament_creation_can_select_unrated(client, isolate_test_database):
    conftest.set_admin_session(client, isolate_test_database)

    response = client.post(
        "/admin/tournaments?lang=en",
        data={
            "action": "create",
            "name": "New unrated event",
            "rounds": "1",
            "tournament_type": "swiss",
            "is_rated_form_present": "1",
        },
    )

    assert response.status_code == 302
    conn = sqlite3.connect(isolate_test_database)
    try:
        assert conn.execute(
            "SELECT is_rated FROM tournaments WHERE name = ?",
            ("New unrated event",),
        ).fetchone() == (0,)
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("initial_is_rated", "new_is_rated", "expect_rating_change"),
    [(1, 0, False), (0, 1, True)],
)
def test_changing_completed_tournament_rated_setting_marks_ratings_dirty(
    client,
    isolate_test_database,
    initial_is_rated,
    new_is_rated,
    expect_rating_change,
):
    db_path = isolate_test_database
    rating_service.recompute_ratings()
    baseline_rating = _player_rating(db_path)

    conn = sqlite3.connect(db_path)
    try:
        tournament_id = _add_tournament(
            conn, "Rated setting event", "completed", include_game=True
        )
        conn.execute(
            "UPDATE tournaments SET is_rated = ? WHERE id = ?",
            (initial_is_rated, tournament_id),
        )
        conn.commit()
    finally:
        conn.close()

    rating_service.recompute_ratings()
    assert (_player_rating(db_path) != baseline_rating) == bool(initial_is_rated)
    rating_service.clear_dirty_date()
    conftest.set_admin_session(client, db_path)

    form_data = {
        "name": "Rated setting event",
        "rounds": "1",
        "bye_points": "1",
        "absent_points": "0",
        "handicap_enabled": "0",
        "is_rated_form_present": "1",
    }
    if new_is_rated:
        form_data["is_rated"] = "1"
    response = client.post(
        f"/admin/tournaments/{tournament_id}/settings?lang=en",
        data=form_data,
    )

    assert response.status_code == 302
    assert rating_service.get_dirty_date() == "2026-08-01"
    rating_service.update_from_latest_snapshot()
    assert (_player_rating(db_path) != baseline_rating) == expect_rating_change


@pytest.mark.parametrize("status", ["draft", "canceled"])
def test_changing_completed_tournament_status_marks_dirty_until_manual_recalculation(
    client, isolate_test_database, status
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
        data={"status": status},
    )

    assert response.status_code == 302
    assert _player_rating(db_path) != baseline_rating
    assert rating_service.get_dirty_date() == "2026-08-01"

    profile = load_player(1, lang="en")
    assert all(
        match["event"] != "Status change event"
        for match in profile["all_matches"]
    )
    assert "Status change event" not in client.get(
        "/matches?lang=en"
    ).get_data(as_text=True)

    response = client.post(
        "/admin/ratings?lang=en", data={"action": "recalculate"}
    )
    assert response.status_code == 302
    assert _player_rating(db_path) == baseline_rating
    assert rating_service.get_dirty_date() is None


def test_automatic_completion_marks_ratings_dirty_without_recomputing(isolate_test_database):
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

    assert _player_rating(db_path) == baseline_rating
    assert rating_service.get_dirty_date() == "2026-08-01"
    rating_service.update_from_latest_snapshot()
    assert _player_rating(db_path) != baseline_rating


def test_incremental_replay_removes_ratings_for_canceled_games(client, isolate_test_database):
    db_path = isolate_test_database
    rating_service.recompute_ratings()
    baseline_rating = _player_rating(db_path)

    conn = sqlite3.connect(db_path)
    try:
        tournament_id = _add_tournament(
            conn, "Incremental status event", "completed", include_game=True
        )
        conn.commit()
    finally:
        conn.close()

    rating_service.recompute_ratings()
    assert _player_rating(db_path) != baseline_rating

    conftest.set_admin_session(client, db_path)
    response = client.post(
        f"/admin/tournaments/{tournament_id}/status?lang=en",
        data={"status": "canceled"},
    )
    assert response.status_code == 302
    assert rating_service.get_dirty_date() == "2026-08-01"

    response = client.post("/admin/ratings?lang=en", data={"action": "update"})
    assert response.status_code == 302
    assert _player_rating(db_path) == baseline_rating
    assert rating_service.get_dirty_date() is None


def test_canceling_materialized_active_tournament_marks_dirty(client, isolate_test_database):
    db_path = isolate_test_database
    rating_service.recompute_ratings()
    baseline_rating = _player_rating(db_path)

    conn = sqlite3.connect(db_path)
    try:
        tournament_id = _add_tournament(
            conn, "Active status event", "active", include_game=True
        )
        conn.commit()
    finally:
        conn.close()

    conftest.set_admin_session(client, db_path)
    response = client.post(
        f"/admin/tournaments/{tournament_id}/status?lang=en",
        data={"status": "canceled"},
    )

    assert response.status_code == 302
    assert _player_rating(db_path) == baseline_rating
    assert rating_service.get_dirty_date() == "2026-08-01"
