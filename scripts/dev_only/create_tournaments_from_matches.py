"""Create and repair tournament records from historical match events.

The command is intentionally opt-in: without ``--apply`` it only reports the
changes it would make. It is idempotent because source matches are recorded in
``match_tournament_migrations`` and linked through ``matches.tournament_pairing_id``.
Participants are created from the source matches so the application's dynamic
standings and player tournament profiles include the migrated results. Existing
rows created by an earlier version of this script are repaired automatically.

Examples (run from the repository root)::

    .venv\Scripts\python.exe scripts\dev_only\create_tournaments_from_matches.py
    .venv\Scripts\python.exe scripts\dev_only\create_tournaments_from_matches.py --apply
    .venv\Scripts\python.exe scripts\dev_only\create_tournaments_from_matches.py --db path\to\acg_ratings.db --apply
"""

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "acg_ratings.db"
EXCLUDED_EVENT_NAMES = {"amistoso", "cc cedritos"}
ROUND_RE = re.compile(r"(?:round|ronda|rodada)?\s*([0-9]+)", re.IGNORECASE)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.tournament_service import (
    _category_for_rating,
    _recalculate_mcmahon_seeds,
    _refresh_tournament_completion_state,
)
from services.reporting_service import ensure_tournament_match_identity


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true", help="write changes; default is a dry run")
    parser.add_argument("--event", help="process only this event name")
    return parser.parse_args()


def excluded_event(event):
    normalized = " ".join(str(event or "").split()).casefold()
    return normalized in EXCLUDED_EVENT_NAMES or "jubango" in normalized


def round_number(match):
    value = match["round_number"] if "round_number" in match.keys() else None
    if value not in (None, ""):
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            pass
    note = str(match["notes"] or "")
    found = ROUND_RE.search(note)
    return max(1, int(found.group(1))) if found else 1


def game_identity(row):
    player_ids = frozenset((row["white_player_id"], row["black_player_id"]))
    if row["result"] == "1-0":
        outcome = row["white_player_id"]
    elif row["result"] == "0-1":
        outcome = row["black_player_id"]
    else:
        outcome = row["result"]
    return row["round_number"], player_ids, outcome


def timestamp():
    return datetime.utcnow().replace(microsecond=0).isoformat(sep=" ")


def infer_system(event, player_count, rounds):
    normalized = str(event or "").casefold()
    if "weiqi" in normalized or (player_count >= 24 and rounds <= 5):
        return "mcmahon"
    return "swiss"


def ensure_tracking_table(conn, apply):
    if not apply:
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS match_tournament_migrations (
            match_id INTEGER PRIMARY KEY,
            tournament_id INTEGER NOT NULL,
            pairing_id INTEGER NOT NULL,
            processed_at TEXT NOT NULL
        )
        """
    )


def ensure_tournament_schema(conn):
    from app import migrate_tournament_schema

    migrate_tournament_schema(conn)
    pairing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(tournament_pairings)").fetchall()
    }
    if "handicap_stones" not in pairing_columns:
        conn.execute(
            "ALTER TABLE tournament_pairings ADD COLUMN handicap_stones INTEGER NOT NULL DEFAULT 0"
        )


def existing_tournament(conn, event):
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tournaments'"
    ).fetchone():
        return None
    return conn.execute(
        """
        SELECT id, pairing_system, tournament_type, source_format
        FROM tournaments
        WHERE lower(trim(name)) = lower(trim(?))
        ORDER BY id
        LIMIT 1
        """,
        (event,),
    ).fetchone()


def create_tournament(conn, event, first_date, last_date, rounds, pairing_system):
    conn.execute(
        """
        INSERT INTO tournaments (
            name, short_name, location, begin_date, end_date, rounds,
            tournament_type, pairing_system, bye_points, absent_points,
            placement_criteria, status, source_format, created_at
        ) VALUES (?, ?, '', ?, ?, ?, ?, ?, 1, 0, 'NBW', 'draft',
                  'Database matches', ?)
        """,
        (event, event[:80], first_date, last_date, rounds, pairing_system, pairing_system, timestamp()),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def ensure_participants(conn, tournament_id, matches):
    player_ids = {match["white_player_id"] for match in matches}
    player_ids.update(match["black_player_id"] for match in matches)
    if not player_ids:
        return 0

    placeholders = ", ".join("?" for _ in player_ids)
    players = conn.execute(
        f"SELECT id, rating FROM players WHERE id IN ({placeholders}) ORDER BY rating DESC, id",
        tuple(player_ids),
    ).fetchall()
    existing = {
        row["player_id"]
        for row in conn.execute(
            "SELECT player_id FROM tournament_participants WHERE tournament_id = ?",
            (tournament_id,),
        ).fetchall()
    }
    tournament = conn.execute(
        "SELECT pairing_system FROM tournaments WHERE id = ?",
        (tournament_id,),
    ).fetchone()
    if tournament is None:
        raise ValueError(f"Tournament {tournament_id} not found")

    added = 0
    next_seed = conn.execute(
        "SELECT COALESCE(MAX(seed_rank), 0) + 1 FROM tournament_participants WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone()[0]
    for player in players:
        if player["id"] in existing:
            continue
        category = _category_for_rating(conn, player["rating"])
        conn.execute(
            """
            INSERT INTO tournament_participants
                (tournament_id, player_id, seed_rating, seed_rank, category, initial_score, acceleration)
            VALUES (?, ?, ?, ?, ?, 0, 0)
            """,
            (tournament_id, player["id"], player["rating"] or 0, next_seed, category),
        )
        next_seed += 1
        added += 1

    if tournament["pairing_system"] == "mcmahon":
        _recalculate_mcmahon_seeds(conn, tournament_id)
    return added


def deduplicate_tournament_pairings(conn, tournament_id):
    rows = conn.execute(
        """
        SELECT p.id, p.board_number, p.white_player_id, p.black_player_id, p.result,
               p.is_bye, r.id AS round_id, r.round_number,
               m.id AS match_id
        FROM tournament_pairings p
        JOIN tournament_rounds r ON r.id = p.round_id
        LEFT JOIN matches m ON m.tournament_pairing_id = p.id
        WHERE r.tournament_id = ?
        ORDER BY r.round_number, p.id
        """,
        (tournament_id,),
    ).fetchall()
    groups = defaultdict(list)
    for row in rows:
        if not row["is_bye"] and row["white_player_id"] is not None and row["black_player_id"] is not None:
            groups[game_identity(row)].append(row)

    removed = 0
    for duplicates in groups.values():
        if len(duplicates) < 2:
            continue
        canonical = sorted(
            duplicates,
            key=lambda row: (
                0 if row["match_id"] is not None else 1,
                0 if 0 < int(row["board_number"] or 0) < 1000 else 1,
                row["id"],
            ),
        )[0]
        canonical_match = canonical["match_id"]
        for duplicate in duplicates:
            if duplicate["id"] == canonical["id"]:
                continue
            duplicate_matches = conn.execute(
                "SELECT id FROM matches WHERE tournament_pairing_id = ? ORDER BY id",
                (duplicate["id"],),
            ).fetchall()
            for match in duplicate_matches:
                if canonical_match is None:
                    conn.execute(
                        "UPDATE matches SET tournament_pairing_id = ? WHERE id = ?",
                        (canonical["id"], match["id"]),
                    )
                    canonical_match = match["id"]
                else:
                    conn.execute("DELETE FROM matches WHERE id = ?", (match["id"],))
                    conn.execute(
                        "DELETE FROM match_tournament_migrations WHERE match_id = ?",
                        (match["id"],),
                    )
            conn.execute(
                "UPDATE match_tournament_migrations SET pairing_id = ? WHERE pairing_id = ?",
                (canonical["id"], duplicate["id"]),
            )
            conn.execute("DELETE FROM tournament_pairings WHERE id = ?", (duplicate["id"],))
            removed += 1
    return removed


def normalize_board_numbers(conn, tournament_id):
    rounds = conn.execute(
        "SELECT id FROM tournament_rounds WHERE tournament_id = ? ORDER BY round_number, id",
        (tournament_id,),
    ).fetchall()
    changed = 0
    for round_row in rounds:
        pairings = conn.execute(
            "SELECT id, board_number FROM tournament_pairings WHERE round_id = ? ORDER BY id",
            (round_row["id"],),
        ).fetchall()
        for pairing in pairings:
            conn.execute(
                "UPDATE tournament_pairings SET board_number = ? WHERE id = ?",
                (-pairing["id"], pairing["id"]),
            )
        for board_number, pairing in enumerate(pairings, 1):
            if pairing["board_number"] != board_number:
                changed += 1
            conn.execute(
                "UPDATE tournament_pairings SET board_number = ? WHERE id = ?",
                (board_number, pairing["id"]),
            )
    return changed


def ensure_round(conn, tournament_id, number, apply):
    row = conn.execute(
        "SELECT id FROM tournament_rounds WHERE tournament_id = ? AND round_number = ?",
        (tournament_id, number),
    ).fetchone()
    if row:
        return row["id"]
    if not apply:
        return None
    conn.execute(
        "INSERT INTO tournament_rounds (tournament_id, round_number, status) VALUES (?, ?, 'completed')",
        (tournament_id, number),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def migrate_match(conn, match, tournament_id, apply):
    if match["tournament_pairing_id"] is not None:
        return "linked"
    tracked = conn.execute(
        "SELECT 1 FROM match_tournament_migrations WHERE match_id = ?",
        (match["id"],),
    ).fetchone()
    if tracked:
        return "tracked"
    round_id = ensure_round(conn, tournament_id, round_number(match), apply)
    if not apply:
        return "pending"
    existing_pairing = conn.execute(
        """
        SELECT id, white_player_id, black_player_id, result
        FROM tournament_pairings
        WHERE round_id = ? AND is_bye = 0
        """,
        (round_id,),
    ).fetchall()
    match_key = game_identity(
        {
            "round_number": round_number(match),
            "white_player_id": match["white_player_id"],
            "black_player_id": match["black_player_id"],
            "result": match["result"],
        }
    )
    for pairing in existing_pairing:
        pairing_key = game_identity(
            {
                "round_number": round_number(match),
                "white_player_id": pairing["white_player_id"],
                "black_player_id": pairing["black_player_id"],
                "result": pairing["result"],
            }
        )
        if pairing_key == match_key:
            conn.execute(
                "UPDATE matches SET tournament_pairing_id = ? WHERE id = ?",
                (pairing["id"], match["id"]),
            )
            conn.execute(
                "INSERT OR REPLACE INTO match_tournament_migrations VALUES (?, ?, ?, ?)",
                (match["id"], tournament_id, pairing["id"], timestamp()),
            )
            return "linked"
    board_number = conn.execute(
        "SELECT COALESCE(MAX(board_number), 0) + 1 FROM tournament_pairings WHERE round_id = ?",
        (round_id,),
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO tournament_pairings (
            round_id, board_number, white_player_id, black_player_id,
            white_player_name, black_player_name, result, is_bye,
            handicap_stones
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, COALESCE(?, 0))
        """,
        (
            round_id,
            board_number,
            match["white_player_id"],
            match["black_player_id"],
            match["white_name"],
            match["black_name"],
            match["result"],
            match["handicap_stones"] if "handicap_stones" in match.keys() else 0,
        ),
    )
    pairing_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "UPDATE matches SET tournament_pairing_id = ? WHERE id = ?",
        (pairing_id, match["id"]),
    )
    conn.execute(
        "INSERT INTO match_tournament_migrations VALUES (?, ?, ?, ?)",
        (match["id"], tournament_id, pairing_id, timestamp()),
    )
    return "created"


def load_matches(conn, event_filter=None):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(matches)").fetchall()}
    has_pairing_column = "tournament_pairing_id" in columns
    pairing_select = "m.tournament_pairing_id" if has_pairing_column else "NULL AS tournament_pairing_id"
    where = ["m.event IS NOT NULL", "trim(m.event) <> ''"]
    if has_pairing_column:
        where.append("m.tournament_pairing_id IS NULL")
    params = []
    if event_filter:
        where.append("lower(trim(m.event)) = lower(trim(?))")
        params.append(event_filter)
    return conn.execute(
        f"""
        SELECT m.*, {pairing_select}, white.display_name AS white_name, black.display_name AS black_name
        FROM matches m
        JOIN players white ON white.id = m.white_player_id
        JOIN players black ON black.id = m.black_player_id
        WHERE {' AND '.join(where)}
        ORDER BY lower(m.event), m.match_date, m.id
        """,
        params,
    ).fetchall()


def load_tracked_matches(conn):
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'match_tournament_migrations'"
    ).fetchone():
        return []
    return conn.execute(
        """
        SELECT m.*, white.display_name AS white_name, black.display_name AS black_name,
               mtm.tournament_id AS migrated_tournament_id
        FROM match_tournament_migrations mtm
        JOIN matches m ON m.id = mtm.match_id
        JOIN players white ON white.id = m.white_player_id
        JOIN players black ON black.id = m.black_player_id
        ORDER BY mtm.tournament_id, m.match_date, m.id
        """
    ).fetchall()


def repair_tracked_tournaments(conn, tracked_matches, apply):
    grouped = defaultdict(list)
    for match in tracked_matches:
        grouped[match["migrated_tournament_id"]].append(match)
    for tournament_id, matches in grouped.items():
        tournament = conn.execute(
            "SELECT name, status, pairing_system, source_format FROM tournaments WHERE id = ?",
            (tournament_id,),
        ).fetchone()
        player_count = len({
            player_id
            for match in matches
            for player_id in (match["white_player_id"], match["black_player_id"])
        })
        inferred_system = infer_system(
            matches[0]["event"] if matches else "",
            player_count,
            max((round_number(match) for match in matches), default=1),
        )
        if not apply:
            placeholders = ", ".join("?" for _ in range(player_count))
            existing_count = conn.execute(
                f"SELECT COUNT(*) FROM tournament_participants WHERE tournament_id = ? AND player_id IN ({placeholders})",
                (tournament_id, *{
                    player_id
                    for match in matches
                    for player_id in (match["white_player_id"], match["black_player_id"])
                }),
            ).fetchone()[0]
            print(
                f"would repair tournament={tournament_id} "
                f"system={inferred_system} tracked_matches={len(matches)} "
                f"participants_to_add={player_count - existing_count}"
            )
            continue
        if tournament and tournament["source_format"] == "Database matches":
            conn.execute(
                "UPDATE tournaments SET tournament_type = ?, pairing_system = ? WHERE id = ?",
                (inferred_system, inferred_system, tournament_id),
            )
        added = ensure_participants(conn, tournament_id, matches)
        removed = deduplicate_tournament_pairings(conn, tournament_id)
        renumbered = normalize_board_numbers(conn, tournament_id)
        _refresh_tournament_completion_state(conn, tournament_id)
        tournament = conn.execute(
            "SELECT name, status, pairing_system FROM tournaments WHERE id = ?",
            (tournament_id,),
        ).fetchone()
        print(
            f"repair tournament={tournament_id} name={tournament['name'] if tournament else '?'} "
            f"system={tournament['pairing_system'] if tournament else '?'} "
            f"status={tournament['status'] if tournament else '?'} "
            f"tracked_matches={len(matches)} participants_added={added} "
            f"pairings_removed={removed} boards_renumbered={renumbered}"
        )


def main():
    args = parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.apply:
            ensure_tournament_schema(conn)
            ensure_tournament_match_identity(conn)
        ensure_tracking_table(conn, args.apply)
        tracked_matches = load_tracked_matches(conn)
        repair_tracked_tournaments(conn, tracked_matches, args.apply)
        groups = defaultdict(list)
        for match in load_matches(conn, args.event):
            if not excluded_event(match["event"]):
                groups[match["event"].strip()].append(match)

        for event, matches in groups.items():
            dates = [str(match["match_date"]) for match in matches if match["match_date"]]
            rounds = max(round_number(match) for match in matches)
            tournament = existing_tournament(conn, event)
            tournament_id = tournament["id"] if tournament else None
            pairing_system = (
                tournament["pairing_system"]
                if tournament is not None
                else infer_system(event, len({player_id for match in matches for player_id in (match["white_player_id"], match["black_player_id"])}), rounds)
            )
            if tournament_id is None and args.apply:
                tournament_id = create_tournament(
                    conn, event, min(dates, default=""), max(dates, default=""), rounds, pairing_system
                )
            action = "reuse" if tournament else "create"
            pending = sum(1 for match in matches if match["tournament_pairing_id"] is None)
            print(
                f"{event}: tournament={action} system={pairing_system} "
                f"matches={len(matches)} pending={pending} rounds={rounds}"
            )
            if args.apply:
                ensure_participants(conn, tournament_id, matches)
                for match in matches:
                    migrate_match(conn, match, tournament_id, True)
                deduplicate_tournament_pairings(conn, tournament_id)
                normalize_board_numbers(conn, tournament_id)
                _refresh_tournament_completion_state(conn, tournament_id)
        if args.apply:
            conn.commit()
        else:
            print("Dry run: no changes written. Use --apply to commit this migration.")
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())