import sqlite3

import pytest


def _add_players_with_games(db_path, first_index, count):
    conn = sqlite3.connect(db_path)
    try:
        player_ids = []
        for index in range(first_index, first_index + count):
            cursor = conn.execute(
                "INSERT INTO players (first_name, last_name, rating, initial_rating, active) VALUES (?, ?, ?, ?, 1)",
                (f"Bench{index}", "Player", 1500 + index, 1500),
            )
            player_ids.append(cursor.lastrowid)
            conn.execute(
                "INSERT INTO rating_snapshots (player_id, snapshot_date, rating, rd, volatility) VALUES (?, '2026-02-01', ?, 88, 0.01)",
                (cursor.lastrowid, 1500 + index),
            )
            conn.execute(
                "INSERT INTO rating_snapshots (player_id, snapshot_date, rating, rd, volatility) VALUES (?, '2026-05-01', ?, 88, 0.01)",
                (cursor.lastrowid, 1510 + index),
            )
        for left, right in zip(player_ids, player_ids[1:]):
            conn.execute(
                """
                INSERT INTO matches (match_date, white_player_id, black_player_id, result, event, round_number)
                VALUES ('2026-03-01', ?, ?, '1-0', 'Benchmark game', 1)
                """,
                (left, right),
            )
        conn.commit()
        return player_ids
    finally:
        conn.close()


@pytest.fixture
def statement_counter(monkeypatch):
    counter = {"count": 0}
    real_connect = sqlite3.connect

    def counting_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(lambda _sql: counter.__setitem__("count", counter["count"] + 1))
        return conn

    monkeypatch.setattr(sqlite3, "connect", counting_connect)
    return counter


def _statements_for(client, counter, url):
    counter["count"] = 0
    response = client.get(url)
    assert response.status_code == 200
    return counter["count"]


PAGES = (
    "/",
    "/rankings",
    "/reports?start_date=2026-01-01&end_date=2026-12-31",
    "/reports/export.csv?start_date=2026-01-01&end_date=2026-12-31",
    "/reports/export.pdf?start_date=2026-01-01&end_date=2026-12-31&player_id=1",
)

STATEMENT_BUDGETS = {
    "/": 40,
    "/rankings": 20,
    "/reports?start_date=2026-01-01&end_date=2026-12-31": 20,
    "/reports/export.csv?start_date=2026-01-01&end_date=2026-12-31": 12,
    "/reports/export.pdf?start_date=2026-01-01&end_date=2026-12-31&player_id=1": 20,
}


def test_public_pages_do_not_issue_queries_per_player(client, isolate_test_database, statement_counter):
    db_path = isolate_test_database
    _add_players_with_games(db_path, 1000, 5)
    small = {page: _statements_for(client, statement_counter, page) for page in PAGES}

    _add_players_with_games(db_path, 2000, 40)
    large = {page: _statements_for(client, statement_counter, page) for page in PAGES}

    for page in PAGES:
        assert large[page] <= small[page] + 2, (page, small[page], large[page])
        assert large[page] <= STATEMENT_BUDGETS[page], (
            page,
            large[page],
            STATEMENT_BUDGETS[page],
        )
