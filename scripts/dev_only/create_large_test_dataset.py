"""Create a deterministic, varied database dataset for local testing.

The generated rows use dedicated prefixes and are removed before each run. The
script is intended for a disposable local database, not production data.

Run from the repository root:
    python scripts/dev_only/create_large_test_dataset.py --dry-run
    python scripts/dev_only/create_large_test_dataset.py --output data/test-large.db
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config

from services import common

PREFIX = "DEMO-DATA-"
DEFAULT_SEED = 20260827
DEFAULT_USERS = 18
DEFAULT_PLAYERS = 180
DEFAULT_MATCHES = 2400
DEFAULT_TOURNAMENTS = 12
DEFAULT_PASSWORD = "demo-password"
ROLES = ("administrator", "tournament_director", "operator")
COUNTRIES = ("ES", "FR", "PT", "GB", "DE", "IT", "NL", "BE")
CLUBS = ("North", "Central", "South", "East", "West", "International")
RESULTS = ("1-0", "0-1", "1/2-1/2")


def initialize_database(output_path):
    config.DB_PATH = str(output_path)
    common.DB_PATH = str(output_path)
    from app import (
        ensure_player_schema_columns,
        init_db,
        migrate_config_schema,
        migrate_matches_notes_schema,
        migrate_tournament_schema,
        normalize_match_round_values,
    )

    init_db()
    conn = common.get_db()
    migrate_tournament_schema(conn)
    migrate_config_schema(conn)
    common.migrate_auth_schema(conn)
    common.migrate_audit_log_schema(conn)
    ensure_player_schema_columns(conn)
    migrate_matches_notes_schema(conn)
    normalize_match_round_values(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(players)").fetchall()}
    if "initial_rating" not in columns:
        conn.execute("ALTER TABLE players ADD COLUMN initial_rating REAL")
    conn.commit()
    return conn


def remove_previous_data(conn):
    player_ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM players WHERE slug LIKE ?", (f"{PREFIX.lower()}%",)
        ).fetchall()
    ]
    tournament_ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM tournaments WHERE name LIKE ?", (f"{PREFIX} %",)
        ).fetchall()
    ]
    user_ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM users WHERE username LIKE ?", (f"{PREFIX.lower()}%",)
        ).fetchall()
    ]

    if player_ids:
        placeholders = ",".join("?" for _ in player_ids)
        conn.execute(f"DELETE FROM matches WHERE white_player_id IN ({placeholders}) OR black_player_id IN ({placeholders})", player_ids * 2)
        conn.execute(f"DELETE FROM rating_snapshots WHERE player_id IN ({placeholders})", player_ids)
        conn.execute(f"DELETE FROM players WHERE id IN ({placeholders})", player_ids)
    if tournament_ids:
        placeholders = ",".join("?" for _ in tournament_ids)
        conn.execute(f"DELETE FROM tournaments WHERE id IN ({placeholders})", tournament_ids)
    if user_ids:
        placeholders = ",".join("?" for _ in user_ids)
        conn.execute(f"DELETE FROM users WHERE id IN ({placeholders})", user_ids)
    conn.commit()
    return len(user_ids), len(player_ids), len(tournament_ids)


def create_users(conn, count, password):
    for index in range(1, count + 1):
        role = ROLES[(index - 1) % len(ROLES)]
        common.create_user_account(
            f"{PREFIX.lower()}user-{index:03d}",
            password,
            role_name=role,
            conn=conn,
        )
    return count


def create_players(conn, count, rng):
    rows = []
    for index in range(1, count + 1):
        first_name = f"DemoFirst{index:03d}"
        last_name = f"DemoLast{index:03d}"
        rating = round(1200 + rng.random() * 900, 2)
        active = 0 if index % 17 == 0 else 1
        rows.append(
            (
                first_name,
                last_name,
                f"{first_name} {last_name}",
                rng.choice(COUNTRIES),
                f"{PREFIX}{rng.choice(CLUBS)}",
                f"{PREFIX.lower()}player-{index:04d}",
                active,
                rating,
                rating,
                round(45 + rng.random() * 90, 2),
                round(0.03 + rng.random() * 0.05, 5),
            )
        )
    conn.executemany(
        """
        INSERT INTO players
            (first_name, last_name, display_name, country, club, slug, active,
             rating, initial_rating, rd, volatility)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return [row[0] for row in conn.execute(
        "SELECT id FROM players WHERE slug LIKE ? ORDER BY id", (f"{PREFIX.lower()}%",)
    ).fetchall()]


def create_matches(conn, player_ids, count, rng):
    start = date(2022, 1, 1)
    rows = []
    for index in range(count):
        white, black = rng.sample(player_ids, 2)
        match_date = start + timedelta(days=rng.randrange(1700))
        rows.append(
            (
                match_date.isoformat(),
                white,
                black,
                rng.choice(RESULTS),
                f"{PREFIX} Event {index % 24 + 1:02d}",
                "Synthetic local test match",
                index % 9 + 1,
            )
        )
    conn.executemany(
        """
        INSERT INTO matches
            (match_date, white_player_id, black_player_id, result, event, notes, round_number)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return count


def create_snapshots(conn, player_ids):
    rows = []
    for player_id in player_ids:
        for snapshot_date, adjustment in (("2023-01-01", 0), ("2024-01-01", 15), ("2025-01-01", -10)):
            rows.append((player_id, snapshot_date, 1500 + adjustment, 80, 0.06))
    conn.executemany(
        "INSERT INTO rating_snapshots (player_id, snapshot_date, rating, rd, volatility) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def create_tournaments(conn, player_ids, count, rng):
    tournament_ids = []
    for index in range(1, count + 1):
        begin_date = date(2023, 1, 1) + timedelta(days=index * 70)
        end_date = begin_date + timedelta(days=2 + index % 5)
        conn.execute(
            """
            INSERT INTO tournaments
                (name, short_name, location, begin_date, end_date, rounds,
                 tournament_type, pairing_system, status, source_format)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{PREFIX} Tournament {index:02d}",
                f"{PREFIX} T{index:02d}",
                rng.choice(COUNTRIES),
                begin_date.isoformat(),
                end_date.isoformat(),
                3 + index % 5,
                "mcmahon" if index % 4 == 0 else "swiss",
                "accelerated_swiss" if index % 3 == 0 else "swiss",
                "completed" if index % 3 else "draft",
                "synthetic local test data",
            ),
        )
        tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        tournament_ids.append(tournament_id)
        selected = rng.sample(player_ids, min(len(player_ids), 12 + index * 3))
        conn.executemany(
            """
            INSERT INTO tournament_participants
                (tournament_id, player_id, seed_rating, seed_rank, initial_score, score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (tournament_id, player_id, 1500 + rng.randrange(-250, 251), rank, 0, rng.randrange(0, 6))
                for rank, player_id in enumerate(selected, 1)
            ],
        )
    return tournament_ids


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create a large deterministic local test dataset.")
    parser.add_argument("--output", default=str(config.DB_PATH), help="SQLite database path to populate.")
    parser.add_argument("--dry-run", action="store_true", help="Show planned counts without changing the database.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--users", type=int, default=DEFAULT_USERS)
    parser.add_argument("--players", type=int, default=DEFAULT_PLAYERS)
    parser.add_argument("--matches", type=int, default=DEFAULT_MATCHES)
    parser.add_argument("--tournaments", type=int, default=DEFAULT_TOURNAMENTS)
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Password assigned to generated users.")
    args = parser.parse_args(argv)

    counts = (args.users, args.players, args.matches, args.tournaments)
    if any(value < 0 for value in counts) or args.users == 0 or args.players < 2:
        raise ValueError("users must be positive and players must be at least 2")
    if args.dry_run:
        print(f"Dry run: would create {args.users} users, {args.players} players, {args.matches} matches, and {args.tournaments} tournaments in {args.output}.")
        return 0

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    original_config_db_path = config.DB_PATH
    original_common_db_path = common.DB_PATH
    conn = initialize_database(output_path)
    rng = random.Random(args.seed)
    try:
        removed = remove_previous_data(conn)
        users = create_users(conn, args.users, args.password)
        player_ids = create_players(conn, args.players, rng)
        matches = create_matches(conn, player_ids, args.matches, rng)
        snapshots = create_snapshots(conn, player_ids)
        tournaments = create_tournaments(conn, player_ids, args.tournaments, rng)
        common.refresh_stats(conn)
        conn.commit()
        print(f"Removed previous demo data: users={removed[0]}, players={removed[1]}, tournaments={removed[2]}")
        print(f"Created: users={users}, players={len(player_ids)}, matches={matches}, snapshots={snapshots}, tournaments={len(tournaments)}")
        print(f"Generated user password: {args.password}")
        return 0
    finally:
        conn.close()
        config.DB_PATH = original_config_db_path
        common.DB_PATH = original_common_db_path


if __name__ == "__main__":
    raise SystemExit(main())
