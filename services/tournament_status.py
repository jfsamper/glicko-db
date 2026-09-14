"""Tournament round completion and status helpers."""


VALID_TOURNAMENT_RESULTS = {"1-0", "0-1", "1/2-1/2", "!0-1", "1-!0", "!0-0"}


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
        "SELECT rounds FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()
    if tournament is None:
        return
    if round_id is None:
        row = conn.execute(
            "SELECT id FROM tournament_rounds WHERE tournament_id = ? ORDER BY round_number DESC LIMIT 1",
            (tournament_id,),
        ).fetchone()
        if row is None:
            conn.execute(
                "UPDATE tournaments SET status = 'draft' WHERE id = ?", (tournament_id,)
            )
            conn.commit()
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
        conn.execute(
            "UPDATE tournaments SET status = 'draft' WHERE id = ?", (tournament_id,)
        )
        conn.commit()
        return
    completed_rounds = sum(
        row["status"] == "completed" or _round_is_complete(conn, row["id"])
        for row in rounds
    )
    final_round = max(row["round_number"] for row in rounds)
    limit = int(tournament["rounds"] or 0)
    conn.execute(
        "UPDATE tournaments SET status = ? WHERE id = ?",
        (
            "completed"
            if limit and final_round >= limit and completed_rounds == len(rounds)
            else "active",
            tournament_id,
        ),
    )
    conn.commit()