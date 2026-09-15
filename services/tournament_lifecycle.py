"""Tournament lifecycle persistence operations."""


def delete_tournament(conn, tournament_id):
    if conn.execute(
        "SELECT id FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone() is None:
        raise ValueError("Tournament not found")
    try:
        conn.execute(
            "DELETE FROM tournament_pairings WHERE round_id IN (SELECT id FROM tournament_rounds WHERE tournament_id = ?)",
            (tournament_id,),
        )
        conn.execute(
            "DELETE FROM tournament_round_players WHERE round_id IN (SELECT id FROM tournament_rounds WHERE tournament_id = ?)",
            (tournament_id,),
        )
        conn.execute(
            "DELETE FROM tournament_rounds WHERE tournament_id = ?", (
                tournament_id,)
        )
        conn.execute(
            "DELETE FROM tournament_participants WHERE tournament_id = ?", (
                tournament_id,)
        )
        conn.execute(
            "DELETE FROM tournament_pending_players WHERE tournament_id = ?", (
                tournament_id,)
        )
        conn.execute(
            "DELETE FROM tournaments WHERE id = ?", (tournament_id,)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
