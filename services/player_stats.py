# services/player_stats.py
"""Service for calculating player statistics and performance metrics."""

def summarize_result(player_id, match):
    if match["white_player_id"] == player_id:
        if match["result"] == "1-0":
            return "X"
        if match["result"] == "0-1":
            return "O"
        return "D"
    if match["result"] == "1-0":
        return "O"
    if match["result"] == "0-1":
        return "X"
    return "D"

def build_player_result_summary(player_id, conn):
    matches = conn.execute(
        "SELECT white_player_id, black_player_id, result FROM matches WHERE white_player_id = ? OR black_player_id = ? ORDER BY match_date DESC LIMIT 8",
        (player_id, player_id),
    ).fetchall()
    return "".join(summarize_result(player_id, match) for match in matches)


def build_recent_result_summaries(conn, limit=8, days=90):
    """Return recent-result strings for players with matches in the last N days."""
    from services.common import server_date

    cutoff = conn.execute(
        "SELECT date(?, '-' || ? || ' days')",
        (server_date().isoformat(), days),
    ).fetchone()[0]
    rows = conn.execute(
        """
        SELECT id, match_date, white_player_id, black_player_id, result
        FROM matches
        WHERE match_date >= ?
        ORDER BY match_date DESC, id DESC
        """,
        (cutoff,),
    ).fetchall()

    summaries = {}
    for match in rows:
        for player_id in (match["white_player_id"], match["black_player_id"]):
            summary = summaries.setdefault(player_id, [])
            if len(summary) < limit:
                summary.append(summarize_result(player_id, match))

    return {
        player_id: "".join(results)
        for player_id, results in summaries.items()
    }

