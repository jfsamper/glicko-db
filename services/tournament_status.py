"""Tournament round completion and status helpers."""
from services.transactions import commit


VALID_TOURNAMENT_RESULTS = {"1-0", "0-1", "1/2-1/2", "!0-1", "1-!0", "!0-0"}
# Absent/default results ("!0-1", "1-!0", "!0-0") count toward tournament
# standings/completion but never represent an actual game, so they must not
# be materialized into the ratings-facing "matches" table.
PLAYED_GAME_RESULTS = {"1-0", "0-1", "1/2-1/2"}


def _earliest_tournament_match_date(conn, tournament_id):
    tables = {
        row[0]
        for row in conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name IN ('matches', 'rating_state',
                           'tournament_pairings', 'tournament_rounds')
            """
        ).fetchall()
    }
    if tables != {
        "matches", "rating_state", "tournament_pairings", "tournament_rounds"
    }:
        return None
    return conn.execute(
        """
        SELECT MIN(m.match_date)
        FROM matches m
        JOIN tournament_pairings p ON p.id = m.tournament_pairing_id
        JOIN tournament_rounds r ON r.id = p.round_id
        WHERE r.tournament_id = ?
        """,
        (tournament_id,),
    ).fetchone()[0]


def mark_ratings_dirty_for_tournament_rating(
    conn, tournament_id, status, previous_is_rated, is_rated
):
    """Mark completed tournament games for replay when their rated setting changes."""
    if status != "completed" or previous_is_rated == is_rated:
        return
    earliest_match_date = _earliest_tournament_match_date(conn, tournament_id)
    if earliest_match_date is not None:
        from services.rating_service import mark_dirty

        mark_dirty(earliest_match_date, conn=conn)


def mark_ratings_dirty_for_tournament_status(
    conn, tournament_id, previous_status, status
):
    changes_rating_history = (
        (previous_status == "completed") != (status == "completed")
        or (
            previous_status == "active"
            and status in {"draft", "canceled"}
        )
    )
    if changes_rating_history:
        earliest_match_date = _earliest_tournament_match_date(conn, tournament_id)
        if earliest_match_date is not None:
            from services.rating_service import mark_dirty

            mark_dirty(earliest_match_date, conn=conn)


def _round_is_complete(conn, round_id):
    total = conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ? AND is_bye = 0",
        (round_id,),
    ).fetchone()[0]
    if total == 0:
        return True
    placeholders = ", ".join("?" for _ in VALID_TOURNAMENT_RESULTS)
    completed = conn.execute(
        f"SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ? AND is_bye = 0 AND result IN ({placeholders})",
        (round_id, *sorted(VALID_TOURNAMENT_RESULTS)),
    ).fetchone()[0]
    return completed == total


def _refresh_tournament_completion_state(conn, tournament_id, round_id=None):
    tournament = conn.execute(
        "SELECT rounds, status FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()
    if tournament is None:
        return
    if round_id is None:
        row = conn.execute(
            "SELECT id FROM tournament_rounds WHERE tournament_id = ? ORDER BY round_number DESC LIMIT 1",
            (tournament_id,),
        ).fetchone()
        if row is None:
            previous_status = tournament["status"]
            conn.execute(
                "UPDATE tournaments SET status = 'draft' WHERE id = ?", (
                    tournament_id,)
            )
            mark_ratings_dirty_for_tournament_status(
                conn, tournament_id, previous_status, "draft"
            )
            commit(conn)
            return
        round_id = row[0]
    if conn.execute(
        "SELECT round_number FROM tournament_rounds WHERE id = ? AND tournament_id = ?",
        (round_id, tournament_id),
    ).fetchone() is None:
        return
    placeholders = ", ".join("?" for _ in VALID_TOURNAMENT_RESULTS)
    played = conn.execute(
        f"SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ? AND is_bye = 0 AND result IN ({placeholders})",
        (round_id, *sorted(VALID_TOURNAMENT_RESULTS)),
    ).fetchone()[0]
    total = conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ? AND is_bye = 0",
        (round_id,),
    ).fetchone()[0]
    if total > 0:
        conn.execute(
            "UPDATE tournament_rounds SET status = ? WHERE id = ?",
            ("completed" if played == total else "scheduled", round_id),
        )
    rounds = conn.execute(
        "SELECT id, round_number, status FROM tournament_rounds WHERE tournament_id = ? ORDER BY round_number",
        (tournament_id,),
    ).fetchall()
    if not rounds:
        previous_status = tournament["status"]
        conn.execute(
            "UPDATE tournaments SET status = 'draft' WHERE id = ?", (
                tournament_id,)
        )
        mark_ratings_dirty_for_tournament_status(
            conn, tournament_id, previous_status, "draft"
        )
        commit(conn)
        return
    completed_rounds = sum(
        row["status"] == "completed" or _round_is_complete(conn, row["id"])
        for row in rounds
    )
    final_round = max(row["round_number"] for row in rounds)
    limit = int(tournament["rounds"] or 0)
    status = (
        "completed"
        if limit and final_round >= limit and completed_rounds == len(rounds)
        else "active"
    )
    conn.execute(
        "UPDATE tournaments SET status = ? WHERE id = ?",
        (status, tournament_id),
    )
    mark_ratings_dirty_for_tournament_status(
        conn, tournament_id, tournament["status"], status
    )
    commit(conn)
