"""Tournament lifecycle persistence operations."""
from services.transactions import transactional


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


@transactional
def copy_tournament(conn, tournament_id, name):
    tournament_columns = [
        row[1] for row in conn.execute("PRAGMA table_info(tournaments)").fetchall()
    ]
    source = conn.execute(
        "SELECT * FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()
    if source is None:
        raise ValueError("Tournament not found")

    source_values = dict(zip(tournament_columns, source))
    copied_columns = [
        column for column in tournament_columns
        if column not in {"id", "created_at"}
    ]
    copied_values = [source_values[column] for column in copied_columns]
    for column, value in {
        "name": name,
        "short_name": None,
        "begin_date": None,
        "end_date": None,
        "status": "draft",
        "source_format": None,
    }.items():
        if column in copied_columns:
            copied_values[copied_columns.index(column)] = value

    placeholders = ", ".join("?" for _ in copied_columns)
    conn.execute(
        f"INSERT INTO tournaments ({', '.join(copied_columns)}) "
        f"VALUES ({placeholders})",
        copied_values,
    )
    copied_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    participant_columns = {
        row[1] for row in conn.execute(
            "PRAGMA table_info(tournament_participants)"
        ).fetchall()
    }
    seed_columns = [
        column for column in (
            "player_id", "seed_rating", "seed_rank", "category",
            "initial_score", "acceleration", "mc_seeds_calculated",
        )
        if column in participant_columns
    ]
    if "player_id" in seed_columns:
        reset_columns = {"score", "received_bye"}
        insert_columns = ["tournament_id", *seed_columns]
        select_values = [
            "0" if column in reset_columns else column
            for column in seed_columns
        ]
        for column in reset_columns.intersection(participant_columns):
            insert_columns.append(column)
            select_values.append("0")
        conn.execute(
            f"INSERT INTO tournament_participants "
            f"({', '.join(insert_columns)}) "
            f"SELECT ?, {', '.join(select_values)} "
            "FROM tournament_participants WHERE tournament_id = ?",
            (copied_id, tournament_id),
        )

    pending_columns = [
        row[1] for row in conn.execute(
            "PRAGMA table_info(tournament_pending_players)"
        ).fetchall()
    ]
    pending_copy_columns = [
        column for column in pending_columns
        if column not in {"id", "tournament_id", "created_at"}
    ]
    if pending_copy_columns:
        conn.execute(
            f"INSERT INTO tournament_pending_players "
            f"(tournament_id, {', '.join(pending_copy_columns)}) "
            f"SELECT ?, {', '.join(pending_copy_columns)} "
            "FROM tournament_pending_players WHERE tournament_id = ?",
            (copied_id, tournament_id),
        )

    return copied_id
