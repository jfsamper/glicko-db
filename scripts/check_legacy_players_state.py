"""Audit managed SQLite files for the historical players_corrupt schema state."""
import json
import re
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGED_PATHS = [ROOT / "data" / "acg_ratings.db"]
MANAGED_PATHS.extend(sorted((ROOT / "backups").glob("*.db")))
fallback_path = ROOT / "data" / "acg_ratings.db.bak"
if fallback_path.exists():
    MANAGED_PATHS.append(fallback_path)

REFERENCE_RE = re.compile(
    r"REFERENCES\s+[\"'`]?players_corrupt[\"'`]?\s*\(",
    re.IGNORECASE,
)


def inspect_database(path):
    result = {
        "path": str(path.relative_to(ROOT)),
        "exists": path.exists(),
        "integrity_check": None,
        "foreign_key_violations": None,
        "has_players": False,
        "has_players_corrupt": False,
        "referencing_tables": [],
    }
    if not path.exists():
        return result

    uri = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        result["has_players"] = "players" in tables
        result["has_players_corrupt"] = "players_corrupt" in tables
        for table_name, create_sql in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND sql IS NOT NULL"
        ).fetchall():
            if table_name in {"players", "players_corrupt"}:
                continue
            if REFERENCE_RE.search(create_sql or ""):
                result["referencing_tables"].append(table_name)
        result["integrity_check"] = conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]
        result["foreign_key_violations"] = len(
            conn.execute("PRAGMA foreign_key_check").fetchall()
        )
    return result


def main():
    results = [inspect_database(path) for path in MANAGED_PATHS]
    print(json.dumps(results, indent=2))
    clean = all(
        result["exists"]
        and result["has_players"]
        and not result["has_players_corrupt"]
        and not result["referencing_tables"]
        and result["integrity_check"] == "ok"
        and result["foreign_key_violations"] == 0
        for result in results
    )
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())