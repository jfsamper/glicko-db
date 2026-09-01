r"""Repair missing BYE pairings in tournaments with odd-sized rosters.

The command is intentionally opt-in: without ``--apply`` it reports the
missing BYEs it found but does not write anything. A repair is made only when
an odd-sized tournament round has no existing BYE and exactly one participant
is not present in a pairing.

Examples (run from the repository root)::

    .venv\Scripts\python.exe scripts\dev_only\repair_tournament_byes.py
    .venv\Scripts\python.exe scripts\dev_only\repair_tournament_byes.py --apply
    .venv\Scripts\python.exe scripts\dev_only\repair_tournament_byes.py --db path\to\acg_ratings.db --apply
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "acg_ratings.db"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true", help="write repairs; default is a dry run")
    return parser.parse_args()


def find_repairs(conn):
    """Return missing BYE candidates grouped by tournament round."""
    tournaments = conn.execute(
        "SELECT id, name FROM tournaments ORDER BY id"
    ).fetchall()
    repairs = []
    for tournament in tournaments:
        participants = {
            row[0]
            for row in conn.execute(
                "SELECT player_id FROM tournament_participants WHERE tournament_id = ?",
                (tournament["id"],),
            ).fetchall()
        }
        if len(participants) % 2 == 0:
            continue

        rounds = conn.execute(
            """
            SELECT id, round_number
            FROM tournament_rounds
            WHERE tournament_id = ?
            ORDER BY round_number, id
            """,
            (tournament["id"],),
        ).fetchall()
        for round_row in rounds:
            pairings = conn.execute(
                """
                SELECT white_player_id, black_player_id, is_bye
                FROM tournament_pairings
                WHERE round_id = ?
                """,
                (round_row["id"],),
            ).fetchall()
            statuses = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT player_id, status FROM tournament_round_players WHERE round_id = ?",
                    (round_row["id"],),
                ).fetchall()
            }
            if any(row["is_bye"] for row in pairings) or "bye" in statuses.values():
                continue

            paired_players = {
                player_id
                for pairing in pairings
                for player_id in (pairing["white_player_id"], pairing["black_player_id"])
                if player_id is not None
            }
            unpaired_players = participants - paired_players
            if len(unpaired_players) != 1:
                continue

            player_id = next(iter(unpaired_players))
            if statuses.get(player_id) == "absent":
                continue
            repairs.append(
                {
                    "tournament_id": tournament["id"],
                    "tournament_name": tournament["name"],
                    "round_id": round_row["id"],
                    "round_number": round_row["round_number"],
                    "player_id": player_id,
                }
            )
    return repairs


def apply_repairs(conn, repairs):
    """Insert the missing BYEs and update the associated tournament state."""
    for repair in repairs:
        board_number = conn.execute(
            "SELECT COALESCE(MAX(board_number), 0) + 1 FROM tournament_pairings WHERE round_id = ?",
            (repair["round_id"],),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO tournament_pairings
                (round_id, board_number, white_player_id, black_player_id, is_bye)
            VALUES (?, ?, ?, NULL, 1)
            """,
            (repair["round_id"], board_number, repair["player_id"]),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO tournament_round_players (round_id, player_id, status)
            VALUES (?, ?, 'bye')
            """,
            (repair["round_id"], repair["player_id"]),
        )
        conn.execute(
            """
            UPDATE tournament_participants
            SET received_bye = 1
            WHERE tournament_id = ? AND player_id = ?
            """,
            (repair["tournament_id"], repair["player_id"]),
        )


def main():
    args = parse_args()
    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        repairs = find_repairs(conn)
        action = "repair" if args.apply else "would repair"
        for repair in repairs:
            print(
                f"{action} tournament={repair['tournament_id']} "
                f"name={repair['tournament_name']} "
                f"round={repair['round_number']} player={repair['player_id']}"
            )
        if args.apply:
            apply_repairs(conn, repairs)
            conn.commit()
        elif repairs:
            print("Dry run: no changes written. Use --apply to commit these repairs.")
        else:
            print("No missing BYEs found.")
    except Exception:
        if args.apply:
            conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()