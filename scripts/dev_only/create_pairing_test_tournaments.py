"""Create repeatable tournament pairing scenarios for local testing.

The script only writes tournament tables. It deliberately does not copy any
pairing results into the main matches table or trigger rating processing.

Run from the repository root:
    python scripts/dev_only/create_pairing_test_tournaments.py --dry-run
    python scripts/dev_only/create_pairing_test_tournaments.py --output data/test-tournaments.db
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from config import DB_PATH, DEFAULT_RATING
from services.tournament_service import add_participant, delete_tournament, generate_next_round

PREFIX = "DEMO-PAIRING-"
DEFAULT_ROUNDS = 5
DEFAULT_SEED = 20260813
PLAYER_COUNTS = (12, 17, 24, 31)
EXTRA_SCENARIOS = (
    ("accelerated_swiss", 17, 1),
    ("swiss_cat", 18, 1),
)
DEFAULT_DRAW_RATE = 0.02


def delete_previous_scenarios(conn):
    tournament_ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM tournaments WHERE name LIKE ?",
            (f"{PREFIX}%",),
        ).fetchall()
    ]
    for tournament_id in tournament_ids:
        delete_tournament(conn, tournament_id)
    return len(tournament_ids)


def choose_players(conn, count, offset):
    players = conn.execute(
        """
        SELECT id, rating, display_name
        FROM players
        WHERE active = 1
        ORDER BY rating DESC, id
        """
    ).fetchall()
    if len(players) < count:
        raise RuntimeError(f"Need {count} active players, found {len(players)}")

    step = max(1, len(players) // count)
    selected = [players[(offset + index * step) % len(players)] for index in range(count)]
    unique = {player["id"]: player for player in selected}
    if len(unique) < count:
        start = offset % len(players)
        rotated = players[start:] + players[:start]
        selected = rotated[:count]
    return selected


def choose_result(rng, white_rating, black_rating, draw_rate=DEFAULT_DRAW_RATE):
    """Return a mostly decisive result while retaining a small draw sample."""
    if not 0 <= draw_rate <= 1:
        raise ValueError("draw_rate must be between 0 and 1")
    if rng.random() < draw_rate:
        return "1/2-1/2"

    stronger_result = "1-0" if white_rating >= black_rating else "0-1"
    weaker_result = "0-1" if stronger_result == "1-0" else "1-0"
    return stronger_result if rng.random() < 0.85 else weaker_result


def create_tournament(
    conn,
    tournament_type,
    count,
    index,
    rng,
    rounds=DEFAULT_ROUNDS,
    draw_rate=DEFAULT_DRAW_RATE,
):
    """Create a single tournament scenario with the given parameters.
    Returns (tournament_id, rounds_created, results_created)."""
    name = f"{PREFIX}{tournament_type.upper()}-{count:02d}-{index:02d}"
    pairing_system = tournament_type
    conn.execute(
        """
        INSERT INTO tournaments
            (name, short_name, location, rounds, tournament_type, pairing_system,
               bye_points, absent_points, placement_criteria, mm_bar, mm_floor,
               mm_zero, status, source_format)
           VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, 8, -30, 0, 'draft', 'local pairing test')
        """,
        (
            name,
            name,
            "Local test data",
            rounds,
            tournament_type,
            pairing_system,
            "MMS,SOSM,SOSOSM" if tournament_type == "mcmahon" else "NBW,SOSW,SOSOSW",
        ),
    )
    tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    players = choose_players(conn, count, index * 3)

    for player in players:
        add_participant(conn, tournament_id, player["id"])

    rounds_created = 0
    results_created = 0
    for _ in range(rounds):
        round_id, pairings = generate_next_round(conn, tournament_id)
        rounds_created += 1
        round_results = []
        for pairing in pairings:
            if pairing["is_bye"]:
                continue
            white = conn.execute(
                "SELECT rating FROM players WHERE id = ?",
                (pairing["white_player_id"],),
            ).fetchone()
            black = conn.execute(
                "SELECT rating FROM players WHERE id = ?",
                (pairing["black_player_id"],),
            ).fetchone()
            result = choose_result(
                rng,
                white["rating"] if white else DEFAULT_RATING,
                black["rating"] if black else DEFAULT_RATING,
                draw_rate,
            )
            round_results.append((pairing, white, black, result))
            conn.execute(
                "UPDATE tournament_pairings SET result = ? WHERE round_id = ? AND white_player_id = ? AND black_player_id = ?",
                (result, round_id, pairing["white_player_id"], pairing["black_player_id"]),
            )
            results_created += 1
        if round_results and all(result == "1/2-1/2" for _, _, _, result in round_results):
            pairing, white, black, _ = round_results[0]
            decisive_result = choose_result(
                rng,
                white["rating"] if white else DEFAULT_RATING,
                black["rating"] if black else DEFAULT_RATING,
                draw_rate=0,
            )
            conn.execute(
                "UPDATE tournament_pairings SET result = ? WHERE round_id = ? AND white_player_id = ? AND black_player_id = ?",
                (decisive_result, round_id, pairing["white_player_id"], pairing["black_player_id"]),
            )
        conn.commit()

    return tournament_id, rounds_created, results_created


def build_plan(seed, rounds):
    rng = random.Random(seed)
    plan = []
    for tournament_type in ("swiss", "mcmahon"):
        for index, count in enumerate(PLAYER_COUNTS, 1):
            plan.append((tournament_type, count, index, rng))
    for tournament_type, count, index in EXTRA_SCENARIOS:
        plan.append((tournament_type, count, index, rng))
    return plan, rounds


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create demo pairing test tournaments for local validation.")
    parser.add_argument("--output", default=str(DB_PATH), help="SQLite database path to populate.")
    parser.add_argument("--dry-run", action="store_true", help="Display the planned creation without changing the database.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Random seed for repeatable tournament generation.")
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS, help="Number of rounds to generate for each tournament.")
    parser.add_argument(
        "--draw-rate",
        type=float,
        default=DEFAULT_DRAW_RATE,
        help="Probability of a drawn game; defaults to 0.02 for mostly decisive examples.",
    )
    args = parser.parse_args(argv)

    if args.rounds <= 0:
        raise ValueError("--rounds must be greater than zero")
    if not 0 <= args.draw_rate <= 1:
        raise ValueError("--draw-rate must be between 0 and 1")

    plan, rounds = build_plan(args.seed, args.rounds)

    if args.dry_run:
        print(f"Dry run: would create {len(plan)} tournament scenarios in {args.output}.")
        for tournament_type, count, index, _ in plan:
            print(f"  {tournament_type} / {count} players / index={index} / rounds={rounds}")
        return 0

    conn = sqlite3.connect(args.output)
    conn.row_factory = sqlite3.Row
    try:
        removed = delete_previous_scenarios(conn)
        created = []
        rng = random.Random(args.seed)
        for tournament_type, count, index, _ in plan:
            tournament_id, rounds_created, results = create_tournament(
                conn,
                tournament_type,
                count,
                index,
                rng,
                args.rounds,
                draw_rate=args.draw_rate,
            )
            created.append((tournament_id, tournament_type, count, rounds_created, results))
        print(f"Removed previous scenarios: {removed}")
        print(f"Created scenarios: {len(created)}")
        for tournament_id, tournament_type, count, rounds_created, results in created:
            print(
                f"  id={tournament_id} type={tournament_type} players={count} "
                f"rounds={rounds_created} results={results}"
            )
        print("Main matches table was not modified; no rating processing was run.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
