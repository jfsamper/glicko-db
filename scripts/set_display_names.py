"""Set players.display_name from first and last names, or from a custom text.

Examples:
    python scripts/set_display_names.py --format first-last --dry-run
    python scripts/set_display_names.py --format last-first
    python scripts/set_display_names.py --format custom --template "{last_name} ({first_name})"

With --format custom, the template may use the {first_name} and {last_name}
placeholders. Text without placeholders is written as-is for every player.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "acg_ratings.db"

FORMATS = ("first-last", "last-first", "custom")
PREVIEW_LIMIT = 10


def build_display_name(first_name, last_name, fmt, template=None):
    first = (first_name or "").strip()
    last = (last_name or "").strip()
    if fmt == "custom":
        return template.replace("{first_name}", first).replace("{last_name}", last).strip()
    if fmt == "first-last":
        return " ".join(part for part in (first, last) if part)
    return ", ".join(part for part in (last, first) if part)


def apply_display_names(conn, fmt, template=None, dry_run=False):
    rows = conn.execute(
        "SELECT id, first_name, last_name, display_name FROM players ORDER BY id"
    ).fetchall()

    changes = []
    unchanged = 0
    skipped = 0
    for row in rows:
        old_name = row["display_name"] or ""
        new_name = build_display_name(row["first_name"], row["last_name"], fmt, template)
        if not new_name:
            skipped += 1
        elif new_name == old_name:
            unchanged += 1
        else:
            changes.append((row["id"], old_name, new_name))

    if not dry_run and changes:
        with conn:
            conn.executemany(
                "UPDATE players SET display_name = ? WHERE id = ?",
                [(new_name, player_id) for player_id, _, new_name in changes],
            )

    stats = {
        "total": len(rows),
        "updated": len(changes),
        "unchanged": unchanged,
        "skipped": skipped,
    }
    return stats, changes


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Set display_name for all players from their first and last names."
    )
    parser.add_argument(
        "--format",
        required=True,
        choices=FORMATS,
        help="first-last: 'firstName lastName'; last-first: 'lastName, firstName'; "
             "custom: use --template.",
    )
    parser.add_argument(
        "--template",
        help="Text for --format custom. May contain {first_name} and {last_name}.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the changes without writing them to the database.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path (default: {DEFAULT_DB_PATH}).",
    )
    args = parser.parse_args(argv)

    if args.format == "custom" and not args.template:
        parser.error("--template is required with --format custom")
    if args.format != "custom" and args.template is not None:
        parser.error("--template can only be used with --format custom")
    if not args.db.exists():
        parser.error(f"database not found: {args.db}")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        stats, changes = apply_display_names(
            conn, args.format, args.template, dry_run=args.dry_run
        )
    finally:
        conn.close()

    for player_id, old_name, new_name in changes[:PREVIEW_LIMIT]:
        print(f"  #{player_id}: {old_name!r} -> {new_name!r}")
    if len(changes) > PREVIEW_LIMIT:
        print(f"  ... and {len(changes) - PREVIEW_LIMIT} more")

    action = "Would update" if args.dry_run else "Updated"
    print(
        f"{action} {stats['updated']} of {stats['total']} players "
        f"({stats['unchanged']} already set, "
        f"{stats['skipped']} skipped because the new name would be empty)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
