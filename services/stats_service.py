"""Owns the aggregate per-player games/wins/losses/draws recompute."""
from services.db import get_db


def refresh_stats(conn=None):
    owns_connection = conn is None
    conn = conn or get_db()
    try:
        conn.execute(
        """
        WITH player_results AS (
            SELECT
                white_player_id AS player_id,
                1 AS games,
                CASE WHEN result = '1-0' THEN 1 ELSE 0 END AS wins,
                CASE WHEN result = '0-1' THEN 1 ELSE 0 END AS losses,
                CASE WHEN result NOT IN ('1-0', '0-1') THEN 1 ELSE 0 END AS draws
            FROM matches
            WHERE result NOT LIKE '!%' AND result NOT LIKE '%!%'
            UNION ALL
            SELECT
                black_player_id AS player_id,
                1 AS games,
                CASE WHEN result = '0-1' THEN 1 ELSE 0 END AS wins,
                CASE WHEN result = '1-0' THEN 1 ELSE 0 END AS losses,
                CASE WHEN result NOT IN ('1-0', '0-1') THEN 1 ELSE 0 END AS draws
            FROM matches
            WHERE result NOT LIKE '!%' AND result NOT LIKE '%!%'
        ),
        totals AS (
            SELECT player_id, SUM(games) AS games, SUM(wins) AS wins,
                   SUM(losses) AS losses, SUM(draws) AS draws
            FROM player_results
            GROUP BY player_id
        )
        UPDATE players
        SET
            games_played = COALESCE((SELECT games FROM totals WHERE totals.player_id = players.id), 0),
            wins = COALESCE((SELECT wins FROM totals WHERE totals.player_id = players.id), 0),
            losses = COALESCE((SELECT losses FROM totals WHERE totals.player_id = players.id), 0),
            draws = COALESCE((SELECT draws FROM totals WHERE totals.player_id = players.id), 0)
        """
        )
        conn.commit()
    finally:
        if owns_connection:
            conn.close()
