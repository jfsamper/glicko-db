"""Generate a disposable workload and benchmark public Flask pages.

Example:
    python scripts/benchmark_public_pages.py --output C:\\temp\\glicko-a0.db

The target database must not already exist. The script creates a deterministic
synthetic fixture and leaves the database in place for inspection or removal.
"""
import argparse
import json
import math
import os
import platform
import random
import sqlite3
import statistics
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config

SEED = 20260827
PAGES = (
    ("/", "/"),
    ("/rankings", "/rankings"),
    ("/players", "/players"),
    ("/matches", "/matches"),
    ("/tournaments", "/tournaments"),
    ("/reports (all time)", "/reports?period=all_time"),
    (
        "/reports (bounded)",
        "/reports?period=custom&start_date=2025-01-01&end_date=2026-12-31",
    ),
    (
        "/reports (selected player)",
        "/reports?period=custom&start_date=2025-01-01&end_date=2026-12-31&player_id=1",
    ),
    (
        "/reports/export.csv",
        "/reports/export.csv?period=custom&start_date=2025-01-01&end_date=2026-12-31",
    ),
    (
        "/reports/export.pdf",
        "/reports/export.pdf?period=custom&start_date=2025-01-01&end_date=2026-12-31&player_id=1",
    ),
    ("/player/view", "/player/view?id=1"),
    ("/category", "/category"),
)


def _create_fixture(database, players_count, matches_count, tournaments_count):
    config.DB_PATH = str(database)
    os.environ["LOAD_SAMPLE_DATA"] = "0"
    from app import initialize_app

    initialize_app()
    rng = random.Random(SEED)
    conn = sqlite3.connect(database)
    try:
        player_rows = [
            (
                f"BenchFirst{index:04d}",
                f"BenchLast{index:04d}",
                ("ES", "FR", "PT", "GB", "DE", "IT", "NL", "BE")[index % 8],
                f"Bench Club {index % 12:02d}",
                f"bench-player-{index:05d}",
                0 if index % 17 == 0 else 1,
                round(1200 + rng.random() * 900, 2),
                1500.0,
                80.0,
                0.06,
            )
            for index in range(1, players_count + 1)
        ]
        conn.executemany(
            """
            INSERT INTO players
                (first_name, last_name, country, club, slug, active, rating,
                 initial_rating, rd, volatility)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            player_rows,
        )
        player_ids = list(
            range(
                conn.execute("SELECT MIN(id) FROM players").fetchone()[0],
                conn.execute("SELECT MIN(id) FROM players").fetchone()[0]
                + players_count,
            )
        )
        start = date(2022, 1, 1)
        match_rows = []
        for index in range(matches_count):
            white_id, black_id = rng.sample(player_ids, 2)
            match_date = start + timedelta(days=rng.randrange(1700))
            match_rows.append(
                (
                    match_date.isoformat(),
                    white_id,
                    black_id,
                    rng.choice(("1-0", "0-1", "1/2-1/2")),
                    f"Benchmark Event {index % 24:02d}",
                    "Synthetic benchmark match",
                    index % 9 + 1,
                )
            )
        conn.executemany(
            """
            INSERT INTO matches
                (match_date, white_player_id, black_player_id, result, event,
                 notes, round_number)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            match_rows,
        )
        conn.executemany(
            """
            INSERT INTO rating_snapshots
                (player_id, snapshot_date, rating, rd, volatility)
            VALUES (?, ?, ?, 80, 0.06)
            """,
            [
                (player_id, snapshot_date, 1500 + adjustment)
                for player_id in player_ids
                for snapshot_date, adjustment in (
                    ("2023-01-01", 0),
                    ("2024-01-01", 15),
                    ("2025-01-01", -10),
                )
            ],
        )
        conn.executemany(
            "INSERT INTO tournaments (name, rounds, status) VALUES (?, 5, ?)",
            [
                (
                    f"Benchmark Tournament {index:03d}",
                    "completed" if index % 3 else "draft",
                )
                for index in range(1, tournaments_count + 1)
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _percentile(values, percentile):
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _database_counts(database):
    conn = sqlite3.connect(database)
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("players", "matches", "rating_snapshots", "tournaments")
        }
    finally:
        conn.close()


def _measure_lock_wait(database):
    acquired = threading.Event()
    release = threading.Event()

    def hold_write_lock():
        conn = sqlite3.connect(database)
        try:
            conn.execute("BEGIN IMMEDIATE")
            acquired.set()
            release.wait(timeout=0.05)
            conn.commit()
        finally:
            conn.close()

    holder = threading.Thread(target=hold_write_lock)
    holder.start()
    if not acquired.wait(timeout=5):
        release.set()
        holder.join(timeout=5)
        raise RuntimeError("Could not acquire the SQLite writer lock for the probe")

    contender = sqlite3.connect(database, timeout=5)
    started = time.perf_counter()
    try:
        contender.execute("BEGIN IMMEDIATE")
        result = {
            "wait_ms": round((time.perf_counter() - started) * 1000, 2),
            "acquired_after_release": True,
        }
        contender.rollback()
    except sqlite3.OperationalError as exc:
        result = {
            "wait_ms": round((time.perf_counter() - started) * 1000, 2),
            "acquired_after_release": False,
            "error": str(exc),
        }
    finally:
        contender.close()
        release.set()
        holder.join(timeout=5)
    if holder.is_alive():
        raise RuntimeError("SQLite lock-probe holder did not stop")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create a synthetic database and benchmark public pages."
    )
    parser.add_argument("--output", required=True, help="New disposable SQLite path.")
    parser.add_argument("--players", type=int, default=1000)
    parser.add_argument("--matches", type=int, default=20000)
    parser.add_argument("--tournaments", type=int, default=20)
    parser.add_argument("--repeat", type=int, default=10)
    args = parser.parse_args(argv)
    if args.players < 2 or args.matches < 0 or args.tournaments < 0:
        parser.error("players must be at least 2; matches/tournaments cannot be negative")
    if args.repeat < 2:
        parser.error("--repeat must be at least 2 to report p95")

    database = Path(args.output).resolve()
    if database.exists():
        parser.error(f"Refusing to overwrite existing database: {database}")
    database.parent.mkdir(parents=True, exist_ok=True)
    _create_fixture(database, args.players, args.matches, args.tournaments)

    statements = {"count": 0}
    real_connect = sqlite3.connect

    def counting_connect(*connect_args, **connect_kwargs):
        conn = real_connect(*connect_args, **connect_kwargs)
        conn.set_trace_callback(
            lambda _sql: statements.__setitem__("count", statements["count"] + 1)
        )
        return conn

    sqlite3.connect = counting_connect
    try:
        config.DB_PATH = str(database)
        from app import create_app

        app = create_app(
            {
                "TESTING": True,
                "WTF_CSRF_ENABLED": False,
                "SKIP_INIT_DB": True,
            },
            auto_init=False,
        )
        client = app.test_client()
        results = []
        for name, url in PAGES:
            for _ in range(2):
                client.get(url)
            timings = []
            counts = []
            statuses = []
            for _ in range(args.repeat):
                statements["count"] = 0
                started = time.perf_counter()
                response = client.get(url)
                timings.append((time.perf_counter() - started) * 1000)
                counts.append(statements["count"])
                statuses.append(response.status_code)
            results.append(
                {
                    "workload": name,
                    "p50_ms": round(statistics.median(timings), 2),
                    "p95_ms": round(_percentile(timings, 0.95), 2),
                    "sql_statements_median": round(statistics.median(counts), 1),
                    "sql_statements_max": max(counts),
                    "http_statuses": sorted(set(statuses)),
                }
            )
    finally:
        sqlite3.connect = real_connect

    print(
        json.dumps(
            {
                "database": str(database),
                "dataset": _database_counts(database),
                "environment": {
                    "os": platform.platform(),
                    "processor": platform.processor() or platform.machine(),
                    "logical_cpus": os.cpu_count(),
                    "python": platform.python_version(),
                    "sqlite": sqlite3.sqlite_version,
                    "seed": SEED,
                    "repeat": args.repeat,
                    "warmup_requests_per_workload": 2,
                },
                "sqlite_write_lock_probe": _measure_lock_wait(database),
                "workloads": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
