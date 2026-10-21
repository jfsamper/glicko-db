import sqlite3

import conftest
from app import app
import services.news_service as news_service
import services.sgf_service as sgf_service
from services.public_visibility import (
    public_match_condition,
    unpublished_tournament_sgf_filenames,
)

SGF_TEXT = "(;GM[1]FF[4]SZ[19]PB[Black]PW[White]RE[W+R];B[pd];W[dd])"


def _execute(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _add_tournament_game(db_path, status, event, sgf_filename=None):
    tournament_id = _execute(
        db_path,
        "INSERT INTO tournaments (name, rounds, status) VALUES (?, 1, ?)",
        (f"{event} cup", status),
    )
    round_id = _execute(
        db_path,
        "INSERT INTO tournament_rounds (tournament_id, round_number, status) VALUES (?, 1, 'completed')",
        (tournament_id,),
    )
    pairing_id = _execute(
        db_path,
        """
        INSERT INTO tournament_pairings
            (round_id, board_number, white_player_id, black_player_id, result, is_bye)
        VALUES (?, 1, 1, 2, '1-0', 0)
        """,
        (round_id,),
    )
    return _execute(
        db_path,
        """
        INSERT INTO matches
            (match_date, white_player_id, black_player_id, result, event, round_number,
             tournament_pairing_id, sgf_filename)
        VALUES ('2026-08-01', 1, 2, '1-0', ?, 1, ?, ?)
        """,
        (event, pairing_id, sgf_filename),
    )


def test_public_match_condition_hides_draft_and_canceled_tournament_games(isolate_test_database):
    db_path = isolate_test_database
    _add_tournament_game(db_path, "draft", "Draft cup game")
    _add_tournament_game(db_path, "canceled", "Canceled cup game")
    _add_tournament_game(db_path, "active", "Live cup game")
    unrated_tournament_id = _execute(
        db_path,
        "INSERT INTO tournaments (name, rounds, status, is_rated) VALUES (?, 1, 'completed', 0)",
        ("Unrated cup",),
    )
    unrated_round_id = _execute(
        db_path,
        "INSERT INTO tournament_rounds (tournament_id, round_number, status) VALUES (?, 1, 'completed')",
        (unrated_tournament_id,),
    )
    unrated_pairing_id = _execute(
        db_path,
        """
        INSERT INTO tournament_pairings
            (round_id, board_number, white_player_id, black_player_id, result, is_bye)
        VALUES (?, 1, 1, 2, '1-0', 0)
        """,
        (unrated_round_id,),
    )
    _execute(
        db_path,
        """
        INSERT INTO matches
            (match_date, white_player_id, black_player_id, result, event, round_number, tournament_pairing_id)
        VALUES ('2026-08-03', 1, 2, '1-0', 'Unrated cup game', 1, ?)
        """,
        (unrated_pairing_id,),
    )
    _execute(
        db_path,
        """
        INSERT INTO matches (match_date, white_player_id, black_player_id, result, event, round_number)
        VALUES ('2026-08-02', 1, 2, '0-1', 'Friendly game', 1)
        """,
    )

    conn = sqlite3.connect(db_path)
    try:
        visible_events = {
            row[0]
            for row in conn.execute(
                f"SELECT m.event FROM matches m WHERE {public_match_condition(conn, 'm')}"
            ).fetchall()
        }
        stored_events = {row[0] for row in conn.execute("SELECT event FROM matches").fetchall()}
    finally:
        conn.close()

    assert "Draft cup game" not in visible_events
    assert "Canceled cup game" not in visible_events
    assert {"Live cup game", "Unrated cup game", "Friendly game"} <= visible_events
    assert "Draft cup game" in stored_events
    assert "Canceled cup game" in stored_events


def test_match_list_hides_draft_and_canceled_games_while_admin_matches_keep_them(isolate_test_database):
    db_path = isolate_test_database
    _add_tournament_game(db_path, "draft", "Hidden draft marker")
    _add_tournament_game(db_path, "canceled", "Hidden canceled marker")

    public_body = app.test_client().get("/matches?lang=en").get_data(as_text=True)
    assert "Hidden draft marker" not in public_body
    assert "Hidden canceled marker" not in public_body

    staff_client = app.test_client()
    conftest.set_admin_session(staff_client, db_path)
    admin_body = staff_client.get("/admin/matches?lang=en").get_data(as_text=True)
    assert "Hidden draft marker" in admin_body
    assert "Hidden canceled marker" in admin_body


def test_completed_unrated_games_are_publicly_labeled(isolate_test_database):
    db_path = isolate_test_database
    tournament_id = _execute(
        db_path,
        "INSERT INTO tournaments (name, rounds, status, is_rated) VALUES (?, 1, 'completed', 0)",
        ("Public unrated cup",),
    )
    round_id = _execute(
        db_path,
        "INSERT INTO tournament_rounds (tournament_id, round_number, status) VALUES (?, 1, 'completed')",
        (tournament_id,),
    )
    pairing_id = _execute(
        db_path,
        """
        INSERT INTO tournament_pairings
            (round_id, board_number, white_player_id, black_player_id, result, is_bye)
        VALUES (?, 1, 1, 2, '1-0', 0)
        """,
        (round_id,),
    )
    _execute(
        db_path,
        """
        INSERT INTO matches
            (match_date, white_player_id, black_player_id, result, event, round_number, tournament_pairing_id)
        VALUES ('2026-08-04', 1, 2, '1-0', 'Public unrated game', 1, ?)
        """,
        (pairing_id,),
    )

    response = app.test_client().get("/matches?lang=en")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Public unrated game" in body
    assert "Unrated" in body


def test_sgf_linked_only_to_unpublished_tournament_game_is_hidden_from_public_pages(
    isolate_test_database, monkeypatch, tmp_path
):
    db_path = isolate_test_database
    upload_dir = tmp_path / "sgf"
    upload_dir.mkdir()
    (upload_dir / "draft-game.sgf").write_text(SGF_TEXT, encoding="utf-8")
    (upload_dir / "live-game.sgf").write_text(SGF_TEXT, encoding="utf-8")
    monkeypatch.setattr(sgf_service, "SGF_UPLOAD_DIR", upload_dir)
    _add_tournament_game(db_path, "draft", "Draft SGF game", "draft-game.sgf")
    (upload_dir / "canceled-game.sgf").write_text(SGF_TEXT, encoding="utf-8")
    _add_tournament_game(db_path, "canceled", "Canceled SGF game", "canceled-game.sgf")
    _add_tournament_game(db_path, "active", "Live SGF game", "live-game.sgf")

    conn = sqlite3.connect(db_path)
    try:
        assert unpublished_tournament_sgf_filenames(conn) == {
            "draft-game.sgf",
            "canceled-game.sgf",
        }
    finally:
        conn.close()

    client = app.test_client()
    library = client.get("/sgf-library?lang=en").get_data(as_text=True)
    assert "draft-game.sgf" not in library
    assert "canceled-game.sgf" not in library
    assert "live-game.sgf" in library
    assert client.get("/sgf/draft-game.sgf").status_code == 404
    assert client.get("/sgf/canceled-game.sgf").status_code == 404
    assert client.get("/sgf-library/draft-game.sgf").status_code == 404
    assert client.get("/sgf/live-game.sgf").status_code == 200


def test_news_tags_for_draft_tournaments_are_hidden_from_public_articles(isolate_test_database):
    db_path = isolate_test_database
    draft_tournament_id = _execute(
        db_path,
        "INSERT INTO tournaments (name, rounds, status) VALUES ('Unpublished news cup', 1, 'draft')",
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        article_id = news_service.save_article(
            conn,
            "Preparations",
            "Registration opens soon.",
            True,
            [{"tag_type": "tournament", "entity_id": draft_tournament_id}],
        )
        public_article = news_service.get_article(conn, article_id, include_drafts=False)
        admin_article = news_service.get_article(conn, article_id)
    finally:
        conn.close()

    assert public_article["tags"] == []
    assert [tag["tag_type"] for tag in admin_article["tags"]] == ["tournament"]
