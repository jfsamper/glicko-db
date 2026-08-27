import csv
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.category_service import glicko_to_category

DB_PATH = ROOT / "data" / "acg_ratings.db"
OUTPUT_FILE = ROOT / "player_categories.csv"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    players = conn.execute(
        """
        SELECT
            id,
            display_name,
            games_played,
            rating
        FROM players
        ORDER BY rating DESC
        """
    ).fetchall()

    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8",
    ) as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "id",
            "display_name",
            "games_played",
            "rating",
            "category",
        ])

        for player in players:
            rating = player["rating"]
            writer.writerow([
                player["id"],
                player["display_name"],
                player["games_played"],
                round(float(rating or 0), 1),
                glicko_to_category(rating, 1),
            ])

    conn.close()
    print(f"Exported {len(players)} players to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
