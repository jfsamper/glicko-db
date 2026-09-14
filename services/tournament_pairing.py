"""Tournament pairing policy and handicap configuration helpers."""
from config import GLICKO_K, GLICKO_M
from services.category_service import suggested_handicap_stones
from services.pairing_service import DEFAULT_CATEGORY_ROUNDS, default_acceleration_rounds

SUPPORTED_SYSTEMS = {"swiss", "swiss_cat", "accelerated_swiss", "mcmahon"}


def normalize_tournament_system(value, default="swiss"):
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized == "swiss_by_category":
        normalized = "swiss_cat"
    return normalized if normalized in SUPPORTED_SYSTEMS else default


def normalize_tournament_rounds(rounds):
    try:
        value = int(rounds)
    except (TypeError, ValueError):
        return 1
    return max(1, value)


def table_columns(conn, table_name):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def pairing_policy(tournament, round_number):
    acceleration_rounds = (
        int(tournament["acceleration_rounds"])
        if "acceleration_rounds" in tournament.keys() and tournament["acceleration_rounds"] is not None
        else default_acceleration_rounds(tournament["rounds"] if "rounds" in tournament.keys() else 1)
    )
    category_rounds = (
        int(tournament["category_rounds"])
        if "category_rounds" in tournament.keys() and tournament["category_rounds"] is not None
        else DEFAULT_CATEGORY_ROUNDS
    )
    return {
        "acceleration_active": tournament["pairing_system"] == "accelerated_swiss" and round_number <= max(0, acceleration_rounds),
        "category_strict": tournament["pairing_system"] == "swiss_cat" and (category_rounds == 0 or round_number <= max(0, category_rounds)),
    }


def auto_handicap_stones(conn, white_player_id, black_player_id):
    rows = conn.execute(
        "SELECT id, rating FROM players WHERE id IN (?, ?)",
        (white_player_id, black_player_id),
    ).fetchall()
    ratings = {row["id"]: row["rating"] for row in rows}
    if white_player_id not in ratings or black_player_id not in ratings:
        return 0
    config = conn.execute(
        "SELECT glicko_k, glicko_m FROM category_config WHERE id = 1"
    ).fetchone() if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'category_config'"
    ).fetchone() else None
    return suggested_handicap_stones(
        ratings[white_player_id], ratings[black_player_id],
        k=config["glicko_k"] if config else GLICKO_K,
        m=config["glicko_m"] if config else GLICKO_M,
    )


def tournament_handicap_enabled(conn, tournament_id):
    columns = table_columns(conn, "tournaments")
    if "handicap_enabled" not in columns:
        return True
    row = conn.execute("SELECT handicap_enabled FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    return bool(row and row["handicap_enabled"])


def update_tournament_handicaps(conn, tournament_id, handicap_enabled, apply_auto_handicap=False):
    pairings = conn.execute(
        """
        SELECT p.id, p.white_player_id, p.black_player_id, p.is_bye
        FROM tournament_pairings p
        JOIN tournament_rounds r ON r.id = p.round_id
        WHERE r.tournament_id = ?
        """,
        (tournament_id,),
    ).fetchall()
    for pairing in pairings:
        handicap_stones = 0
        if handicap_enabled and apply_auto_handicap and not pairing["is_bye"]:
            if pairing["white_player_id"] and pairing["black_player_id"]:
                handicap_stones = auto_handicap_stones(conn, pairing["white_player_id"], pairing["black_player_id"])
        conn.execute("UPDATE tournament_pairings SET handicap_stones = ? WHERE id = ?", (handicap_stones, pairing["id"]))
        conn.execute("UPDATE matches SET handicap_stones = ? WHERE tournament_pairing_id = ?", (handicap_stones, pairing["id"]))
