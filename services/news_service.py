"""News articles and validated links to application entities."""
import re

from services.db import get_db
from services.sgf_service import get_sgf_path

TAG_TYPES = ("player", "tournament", "match")
TAG_TOKEN_PREFIXES = {tag_type: f"[{tag_type}:" for tag_type in TAG_TYPES}
NEWS_TAG_TOKEN_RE = re.compile(r"\[(player|tournament|match):(\d+)\]")


def migrate_news_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS news_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            is_published INTEGER NOT NULL DEFAULT 0,
            published_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS news_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER NOT NULL,
            tag_type TEXT NOT NULL CHECK(tag_type IN ('player', 'tournament', 'match')),
            entity_id INTEGER NOT NULL,
            UNIQUE(article_id, tag_type, entity_id),
            FOREIGN KEY(article_id) REFERENCES news_articles(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_news_articles_published
            ON news_articles(is_published, published_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_news_tags_article
            ON news_tags(article_id);
        """
    )
    conn.commit()


def _tag_target(conn, tag_type, entity_id):
    table = {"player": "players", "tournament": "tournaments",
             "match": "matches"}.get(tag_type)
    if table is None:
        return None
    return conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (entity_id,)).fetchone()


def _load_tag(conn, tag_type, entity_id):
    if tag_type == "match":
        target = conn.execute(
            """
            SELECT m.id, m.sgf_filename,
                   printf('%s: %s vs %s', m.match_date, white.display_name, black.display_name) AS label
            FROM matches m
            JOIN players white ON white.id = m.white_player_id
            JOIN players black ON black.id = m.black_player_id
            WHERE m.id = ?
            """,
            (entity_id,),
        ).fetchone()
    else:
        table = "players" if tag_type == "player" else "tournaments"
        label_column = "display_name" if tag_type == "player" else "name"
        target = conn.execute(
            f"SELECT id, {label_column} AS label FROM {table} WHERE id = ?",
            (entity_id,),
        ).fetchone()
    if target is None:
        return None
    tag = {"tag_type": tag_type, "entity_id": entity_id, **dict(target)}
    if tag_type == "match":
        tag["has_sgf"] = bool(get_sgf_path(tag.get("sgf_filename")))
    return tag


def resolve_news_tags(conn, body, tags):
    """Resolve saved tags plus valid tokens typed directly into article text."""
    resolved = list(tags or [])
    known = {(tag["tag_type"], int(tag["entity_id"])) for tag in resolved}
    for match in NEWS_TAG_TOKEN_RE.finditer(body or ""):
        key = (match.group(1), int(match.group(2)))
        if key in known:
            continue
        tag = _load_tag(conn, *key)
        if tag is not None:
            resolved.append(tag)
            known.add(key)
    return resolved


def normalize_tags(conn, raw_tags):
    tags = []
    for raw_tag in raw_tags or []:
        tag_type = str(raw_tag.get("tag_type", "")).strip().lower()
        try:
            entity_id = int(raw_tag.get("entity_id"))
        except (TypeError, ValueError):
            continue
        if tag_type in TAG_TYPES and entity_id > 0 and _tag_target(conn, tag_type, entity_id):
            tag = (tag_type, entity_id)
            if tag not in tags:
                tags.append(tag)
    return tags


def save_article(conn, title, body, is_published, tags, article_id=None):
    title = (title or "").strip()
    body = (body or "").strip()
    if not title or not body:
        raise ValueError("News title and body are required")

    published_at = "CURRENT_TIMESTAMP" if is_published else "NULL"
    if article_id is None:
        cursor = conn.execute(
            f"INSERT INTO news_articles (title, body, is_published, published_at) VALUES (?, ?, ?, {published_at})",
            (title, body, int(bool(is_published))),
        )
        article_id = cursor.lastrowid
    else:
        exists = conn.execute(
            "SELECT 1 FROM news_articles WHERE id = ?", (article_id,)).fetchone()
        if exists is None:
            raise ValueError("News article not found")
        conn.execute(
            f"UPDATE news_articles SET title = ?, body = ?, is_published = ?, published_at = {published_at}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (title, body, int(bool(is_published)), article_id),
        )
        conn.execute(
            "DELETE FROM news_tags WHERE article_id = ?", (article_id,))

    for tag_type, entity_id in normalize_tags(conn, tags):
        conn.execute(
            "INSERT INTO news_tags (article_id, tag_type, entity_id) VALUES (?, ?, ?)",
            (article_id, tag_type, entity_id),
        )
    conn.commit()
    return article_id


def delete_article(conn, article_id):
    if conn.execute("DELETE FROM news_articles WHERE id = ?", (article_id,)).rowcount == 0:
        raise ValueError("News article not found")
    conn.commit()


def _article_rows(conn, where="", params=(), limit=None):
    limit_clause = " LIMIT ?" if limit is not None else ""
    query_params = (*params, limit) if limit is not None else params
    rows = conn.execute(
        f"SELECT id, title, body, is_published, published_at, created_at, updated_at FROM news_articles {where} ORDER BY COALESCE(published_at, updated_at) DESC, id DESC{limit_clause}",
        query_params,
    ).fetchall()
    articles = [dict(row) for row in rows]
    for article in articles:
        article["tags"] = []
        for tag in conn.execute(
            "SELECT tag_type, entity_id FROM news_tags WHERE article_id = ? ORDER BY id",
            (article["id"],),
        ).fetchall():
            tag = dict(tag)
            target = _load_tag(conn, tag["tag_type"], tag["entity_id"])
            if target:
                tag.update(dict(target))
                article["tags"].append(tag)
    return articles


def list_articles(conn=None, published_only=False, limit=None):
    owns_conn = conn is None
    if conn is None:
        conn = get_db()
    try:
        where = "WHERE is_published = 1" if published_only else ""
        return _article_rows(conn, where, limit=limit)
    finally:
        if owns_conn:
            conn.close()


def get_article(conn, article_id):
    articles = _article_rows(conn, "WHERE id = ?", (article_id,))
    return articles[0] if articles else None


def tag_options(conn):
    return {
        "player": [dict(row) for row in conn.execute("SELECT id, display_name AS label FROM players ORDER BY display_name").fetchall()],
        "tournament": [dict(row) for row in conn.execute("SELECT id, name AS label FROM tournaments ORDER BY COALESCE(begin_date, created_at) DESC, name").fetchall()],
        "match": [dict(row) for row in conn.execute("""
            SELECT m.id, printf('%s: %s vs %s', m.match_date, white.display_name, black.display_name) AS label,
                   m.sgf_filename
            FROM matches m JOIN players white ON white.id = m.white_player_id JOIN players black ON black.id = m.black_player_id
            ORDER BY m.match_date DESC, m.id DESC
        """).fetchall()],
    }
