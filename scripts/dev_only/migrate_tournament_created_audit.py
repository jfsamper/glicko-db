"""Remove tournament names and other extra fields from historical creation logs.

The default mode is a dry run. Pass ``--apply`` to update the selected DB.

Examples (run from the repository root)::

    .venv\Scripts\python.exe scripts\dev_only\migrate_tournament_created_audit.py
    .venv\Scripts\python.exe scripts\dev_only\migrate_tournament_created_audit.py --db path\to\copy.db --apply
"""

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "acg_ratings.db"


def plan_migration(conn):
    """Return rows whose JSON details can be reduced to a tournament ID."""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'audit_log'"
    ).fetchone() is None:
        raise RuntimeError("The selected database does not contain an audit_log table.")

    changes = []
    skipped = 0
    rows = conn.execute(
        """
        SELECT id, details
        FROM audit_log
        WHERE action_type = 'tournament_created'
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        try:
            details = json.loads(row["details"])
        except (TypeError, ValueError):
            skipped += 1
            continue

        if not isinstance(details, dict) or "tournament_id" not in details:
            skipped += 1
            continue

        new_details = json.dumps(
            {"tournament_id": details["tournament_id"]},
            ensure_ascii=False,
            sort_keys=True,
        )
        if row["details"] != new_details:
            changes.append((row["id"], new_details))

    return changes, skipped


def migrate(conn):
    """Atomically drop non-ID fields from historical tournament-created logs."""
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    try:
        changes, skipped = plan_migration(conn)
        conn.executemany(
            "UPDATE audit_log SET details = ? WHERE id = ?",
            [(details, row_id) for row_id, details in changes],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(changes), skipped


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the migration; by default only report the number of affected rows",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.db.is_file():
        raise FileNotFoundError(f"Database file not found: {args.db}")

    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if args.apply:
            changed, skipped = migrate(conn)
            print(f"Updated {changed} tournament_created audit row(s).")
        else:
            changes, skipped = plan_migration(conn)
            print(f"Would update {len(changes)} tournament_created audit row(s).")
            print("Dry run only; no database changes were written.")
        if skipped:
            print(
                f"Skipped {skipped} row(s) with invalid details or no tournament_id."
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
