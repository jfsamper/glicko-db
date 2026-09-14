import sqlite3

from services.news_service import list_articles, save_article


def test_admin_can_publish_news_with_entity_tags_and_public_page(admin_client, isolate_test_database):
    db_path = isolate_test_database
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO tournaments (name, status, rounds, tournament_type, pairing_system) VALUES (?, 'completed', 1, 'swiss', 'swiss')",
            ("Club Cup",),
        )
        conn.commit()

    response = admin_client.post(
        "/admin/news/new?lang=en",
        data={
            "title": "Club Cup report",
            "body": "A strong finish for [player:1] at [tournament:1].",
            "is_published": "1",
            "tag_type": ["player", "tournament", ""],
            "entity_id": ["1", "1", ""],
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/news?lang=en")

    with sqlite3.connect(db_path) as conn:
        article = conn.execute(
            "SELECT id, is_published FROM news_articles WHERE title = ?",
            ("Club Cup report",),
        ).fetchone()
        tags = conn.execute(
            "SELECT tag_type, entity_id FROM news_tags WHERE article_id = ? ORDER BY id",
            (article[0],),
        ).fetchall()
    assert article[1] == 1
    assert tags == [("player", 1), ("tournament", 1)]

    homepage = admin_client.get("/?lang=en")
    assert homepage.status_code == 200
    homepage_body = homepage.get_data(as_text=True)
    assert "Club Cup report" in homepage_body
    assert 'href="/player/view?id=1&amp;lang=en"' in homepage_body
    assert "[player:1]" not in homepage_body

    article_page = admin_client.get(f"/news/{article[0]}?lang=en")
    assert article_page.status_code == 200
    article_body = article_page.get_data(as_text=True)
    assert "A strong finish for" in article_body
    assert 'href="/player/view?id=1&amp;lang=en"' in article_body
    assert "Club Cup" in article_body
    assert "[player:1]" not in article_body


def test_news_editor_exposes_insertable_tag_tokens(admin_client):
    response = admin_client.get("/admin/news/new?lang=en")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "news-insert-tag" in body
    assert "Insert into text" in body
    assert "data-tag-type=\"player\"" in body
    assert "Add another link" in body


def test_news_articles_accept_more_than_four_entity_tags(isolate_test_database):
    with sqlite3.connect(isolate_test_database) as conn:
        article_id = save_article(
            conn,
            "Many links",
            "See [player:1] [player:2] [match:1] [match:2] [match:3].",
            True,
            [
                {"tag_type": "player", "entity_id": "1"},
                {"tag_type": "player", "entity_id": "2"},
                {"tag_type": "match", "entity_id": "1"},
                {"tag_type": "match", "entity_id": "2"},
                {"tag_type": "match", "entity_id": "3"},
            ],
        )
        tag_count = conn.execute(
            "SELECT COUNT(*) FROM news_tags WHERE article_id = ?", (article_id,)
        ).fetchone()[0]
    assert tag_count == 5


def test_invalid_news_token_is_escaped_and_not_linked(admin_client, isolate_test_database):
    with sqlite3.connect(isolate_test_database) as conn:
        article_id = save_article(conn, "Tokens", "Unknown [player:999999] and <b>text</b>", True, [])

    response = admin_client.get(f"/news/{article_id}?lang=en")
    body = response.get_data(as_text=True)
    assert "[player:999999]" in body
    assert "&lt;b&gt;text&lt;/b&gt;" in body


def test_direct_news_tokens_link_without_saved_tag_rows(admin_client, isolate_test_database):
    with sqlite3.connect(isolate_test_database) as conn:
        conn.execute(
            "INSERT INTO tournaments (name, status, rounds, tournament_type, pairing_system) VALUES (?, 'completed', 1, 'swiss', 'swiss')",
            ("Recovered Cup",),
        )
        article_id = save_article(
            conn,
            "Recovered links",
            "See [player:1] and [tournament:1].",
            True,
            [],
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM news_tags WHERE article_id = ?", (article_id,)
        ).fetchone()[0] == 0

    article_page = admin_client.get(f"/news/{article_id}?lang=en")
    body = article_page.get_data(as_text=True)
    assert article_page.status_code == 200
    assert 'href="/player/view?id=1&amp;lang=en"' in body
    assert "Recovered Cup" in body
    assert "[player:1]" not in body

    homepage = admin_client.get("/?lang=en").get_data(as_text=True)
    assert 'href="/player/view?id=1&amp;lang=en"' in homepage
    assert "Recovered Cup" in homepage


def test_unpublished_news_is_not_public(admin_client, isolate_test_database):
    with sqlite3.connect(isolate_test_database) as conn:
        article_id = save_article(conn, "Draft", "Internal", False, [])

    assert admin_client.get(f"/news/{article_id}?lang=en").status_code == 404
    assert "Draft" not in admin_client.get("/?lang=en").get_data(as_text=True)


def test_invalid_news_tags_are_ignored_and_unknown_article_delete_fails_safely(admin_client, isolate_test_database):
    with sqlite3.connect(isolate_test_database) as conn:
        article_id = save_article(
            conn,
            "Tagged",
            "Body",
            True,
            [{"tag_type": "player", "entity_id": "999999"}, {"tag_type": "unknown", "entity_id": "1"}],
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM news_tags WHERE article_id = ?", (article_id,)
        ).fetchone()[0] == 0

    response = admin_client.post("/admin/news/999999/delete?lang=en", follow_redirects=False)
    assert response.status_code == 302
