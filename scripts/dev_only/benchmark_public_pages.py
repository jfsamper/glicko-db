"""Time public pages and count SQL statements against a database, for before/after comparisons.

Usage: python scripts/dev_only/benchmark_public_pages.py <database.db> [--repeat 3]
Point it at a generated copy (see create_large_test_dataset.py), not at production data.
"""
import argparse
import sqlite3
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config

PAGES = (
    "/",
    "/rankings",
    "/players",
    "/matches",
    "/tournaments",
    "/reports?start_date=2025-01-01&end_date=2026-12-31",
    "/reports?start_date=2026-01-01&end_date=2026-06-30&player_id=1",
    "/reports/export.csv?start_date=2025-01-01&end_date=2026-12-31",
    "/player/view?id=1",
    "/category",
)


def main():
    parser = argparse.ArgumentParser(description="Benchmark public pages.")
    parser.add_argument("database")
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()

    config.DB_PATH = str(Path(args.database).resolve())

    statements = {"count": 0}
    real_connect = sqlite3.connect

    def counting_connect(*connect_args, **connect_kwargs):
        conn = real_connect(*connect_args, **connect_kwargs)
        conn.set_trace_callback(lambda _sql: statements.__setitem__("count", statements["count"] + 1))
        return conn

    sqlite3.connect = counting_connect

    from app import create_app, initialize_app

    initialize_app()
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "SKIP_INIT_DB": True}, auto_init=False)
    client = app.test_client()

    print(f"{'page':<70} {'median ms':>10} {'statements':>11} {'status':>7}")
    for page in PAGES:
        client.get(page)
        timings = []
        counts = []
        status = None
        for _ in range(args.repeat):
            statements["count"] = 0
            started = time.perf_counter()
            response = client.get(page)
            timings.append((time.perf_counter() - started) * 1000)
            counts.append(statements["count"])
            status = response.status_code
        print(f"{page:<70} {statistics.median(timings):>10.1f} {max(counts):>11} {status:>7}")


if __name__ == "__main__":
    main()
