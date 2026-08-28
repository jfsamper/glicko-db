"""Utility script to display the schema of the ``tournaments`` table.

The script imports the project's common utilities to obtain a SQLite
connection (via :func:`services.common.get_db`). It then queries the
``PRAGMA table_info`` for the ``tournaments`` table and prints the result.

Run the script using the project's virtual‑environment interpreter, e.g.:

```.venv\Scripts\python.exe scripts\dev_only\check_tournament_schema.py```

This ensures the correct ``BASE_DIR`` and database path are used.
"""

import os
import sys

# Ensure the repository root is on the import path so that ``services`` can be imported.
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if project_root not in sys.path:
    sys.path.append(project_root)

from services.common import get_db


def main() -> None:
    conn = get_db()
    rows = conn.execute("PRAGMA table_info(tournaments)").fetchall()
    # Print each column definition (cid, name, type, notnull, dflt_value, pk)
    for row in rows:
        # row is a sqlite3.Row; access by column name for readability
        print({
            "cid": row["cid"],
            "name": row["name"],
            "type": row["type"],
            "notnull": row["notnull"],
            "dflt_value": row["dflt_value"],
            "pk": row["pk"],
        })
    conn.close()


if __name__ == "__main__":
    main()