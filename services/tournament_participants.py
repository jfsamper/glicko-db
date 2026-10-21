"""Participant lookup and OpenGotha name reconciliation helpers."""
from difflib import SequenceMatcher

from config import GLICKO_K, GLICKO_M
from services.category_service import glicko_to_category
from services.helpers import normalize_key, normalize_text
from services.pairing_service import mcmahon_score_from_rank
from services.player_service import build_player_lookup, ensure_player
from services.transactions import commit, transactional
from services.tournament_pairing import table_columns


def _category_for_rating(conn, rating):
    config = (
        conn.execute(
            "SELECT glicko_k, glicko_m FROM category_config WHERE id = 1"
        ).fetchone()
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'category_config'"
        ).fetchone()
        else None
    )
    category = glicko_to_category(
        rating,
        k=config["glicko_k"] if config else GLICKO_K,
        m=config["glicko_m"] if config else GLICKO_M,
    )
    if category.endswith(" dan"):
        return f"{category[:-4]}D"
    if category.endswith(" kyu"):
        return f"{category[:-4]}K"
    return category


def player_lookup(conn):
    rows = conn.execute(
        "SELECT id, display_name, first_name, last_name, rating FROM players_display"
    ).fetchall()
    lookup = {}
    for row in rows:
        for value in (
            row["display_name"],
            f"{row['first_name']} {row['last_name']}",
            f"{row['last_name']}{row['first_name']}",
        ):
            if value:
                lookup[normalize_key(value)] = row
    return lookup


def _name_tokens(value):
    text = normalize_text(value).lower()
    if not text:
        return []
    tokens = []
    for raw in text.replace("-", " ").split():
        token = normalize_key(raw)
        if token:
            tokens.append(token)
    return tokens


def name_similarity_score(target_name, candidate_name):
    target_tokens = _name_tokens(target_name)
    candidate_tokens = _name_tokens(candidate_name)
    if not target_tokens or not candidate_tokens:
        return SequenceMatcher(
            None, normalize_key(target_name), normalize_key(candidate_name)
        ).ratio()

    target_set = set(target_tokens)
    candidate_set = set(candidate_tokens)
    if target_set.issubset(candidate_set) or candidate_set.issubset(target_set):
        return 0.95

    common_tokens = len(target_set & candidate_set)
    if common_tokens:
        overlap = common_tokens / max(len(target_set), len(candidate_set))
        ordered_ratio = SequenceMatcher(
            None,
            " ".join(sorted(target_tokens)),
            " ".join(sorted(candidate_tokens)),
        ).ratio()
        return max(overlap, ordered_ratio)

    return SequenceMatcher(
        None,
        " ".join(sorted(target_tokens)),
        " ".join(sorted(candidate_tokens)),
    ).ratio()


def suggest_player_match(name, conn):
    """Return (suggested_text, player_id) for the closest active player, or None."""
    if not name:
        return None
    rows = conn.execute(
        "SELECT id, display_name, first_name, last_name FROM players_display WHERE active = 1"
    ).fetchall()
    best_match = None
    best_score = 0.0
    for row in rows:
        for candidate in (row["display_name"], f"{row['first_name']} {row['last_name']}"):
            if not candidate:
                continue
            score = name_similarity_score(name, candidate)
            if score > best_score:
                best_score = score
                best_match = (candidate, row["id"])
    return best_match if best_score >= 0.82 else None


def suggest_player_name(name, conn):
    """Return a close active player name when exact lookup fails."""
    match = suggest_player_match(name, conn)
    return match[0] if match else None


def pending_initial_score(tournament, rank):
    """Return the McMahon starting score for a pending player, or 0.0 for other systems."""
    if not tournament or tournament["pairing_system"] != "mcmahon":
        return 0.0
    keys = tournament.keys()

    def setting(column, default):
        return tournament[column] if column in keys and tournament[column] is not None else default

    return mcmahon_score_from_rank(
        max(1, int(rank or 1)),
        bar=setting("mm_bar", 8), floor=setting("mm_floor", -30), zero=setting("mm_zero", 0))


def list_pending_players(conn, tournament_id):
    """Return pending, not-yet-created players for a tournament."""
    return conn.execute(
        """
        SELECT *
        FROM tournament_pending_players_display
        WHERE tournament_id = ?
        ORDER BY rank, display_name
        """,
        (tournament_id,),
    ).fetchall()


def list_tournament_participants(conn, tournament_id):
    tournament_columns = table_columns(conn, "tournaments")
    select_sql = "SELECT pairing_system"
    if {"mm_bar", "mm_floor", "mm_zero"}.issubset(tournament_columns):
        select_sql += ", mm_bar, mm_floor, mm_zero"
    tournament = conn.execute(
        select_sql + " FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    matched = conn.execute("""
        SELECT tp.player_id AS player_id, p.display_name AS display_name,
               tp.seed_rating, tp.seed_rank, tp.category, tp.initial_score, tp.acceleration
        FROM tournament_participants tp JOIN players_display p ON p.id = tp.player_id
        WHERE tp.tournament_id = ?
    """, (tournament_id,)).fetchall()
    pending = list_pending_players(conn, tournament_id)
    rows = [{"id": row["player_id"], "player_id": row["player_id"], "display_name": row["display_name"], "seed_rating": row["seed_rating"], "seed_rank": row["seed_rank"],
             "category": row["category"], "initial_score": row["initial_score"], "acceleration": row["acceleration"], "is_pending": False} for row in matched]
    rows.extend({"id": None, "pending_id": row["id"], "player_id": -row["id"], "display_name": row["display_name"], "seed_rating": row["rating"], "seed_rank": row["rank"],
                "category": row["category"], "initial_score": pending_initial_score(tournament, row["rank"]), "acceleration": 0.0, "is_pending": True} for row in pending)
    rows.sort(key=lambda row: (row["seed_rank"]
              or 0, str(row["display_name"] or "")))
    return rows


def mcmahon_category_strength(category):
    text = str(category or "").strip().upper()
    try:
        return float(int(text[:-1])) * (-1 if text.endswith("K") else 1) if text.endswith(("D", "K")) else 0.0
    except ValueError:
        return 0.0


def recalculate_mcmahon_seeds(conn, tournament_id):
    columns = table_columns(conn, "tournaments")
    participant_columns = table_columns(conn, "tournament_participants")
    select_sql = "SELECT pairing_system, tournament_type"
    if {"mm_bar", "mm_floor", "mm_zero"}.issubset(columns):
        select_sql += ", mm_bar, mm_floor, mm_zero"
    tournament = conn.execute(
        select_sql + " FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    if tournament is None or tournament["pairing_system"] != "mcmahon":
        return
    if "mc_seeds_calculated" in participant_columns and conn.execute("SELECT COUNT(*) FROM tournament_participants WHERE tournament_id = ? AND mc_seeds_calculated = 0", (tournament_id,)).fetchone()[0] == 0:
        return
    mm_bar = tournament["mm_bar"] if "mm_bar" in tournament.keys(
    ) and tournament["mm_bar"] is not None else 8
    mm_floor = tournament["mm_floor"] if "mm_floor" in tournament.keys(
    ) and tournament["mm_floor"] is not None else -30
    mm_zero = tournament["mm_zero"] if "mm_zero" in tournament.keys(
    ) and tournament["mm_zero"] is not None else 0
    rows = conn.execute("SELECT tp.id, tp.player_id, p.display_name, tp.seed_rating, tp.category FROM tournament_participants tp JOIN players_display p ON p.id = tp.player_id WHERE tp.tournament_id = ?", (tournament_id,)).fetchall()
    ordered = sorted(rows, key=lambda row: (-float(row["seed_rating"] or 0), -mcmahon_category_strength(
        row["category"]), str(row["display_name"] or "").casefold(), int(row["player_id"])))
    for seed_rank, row in enumerate(ordered, 1):
        initial_score = mcmahon_score_from_rank(
            seed_rank, bar=mm_bar, floor=mm_floor, zero=mm_zero)
        if "mc_seeds_calculated" in participant_columns:
            conn.execute("UPDATE tournament_participants SET seed_rank = ?, initial_score = ?, mc_seeds_calculated = 1 WHERE id = ?",
                         (seed_rank, initial_score, row["id"]))
        else:
            conn.execute("UPDATE tournament_participants SET seed_rank = ?, initial_score = ? WHERE id = ?",
                         (seed_rank, initial_score, row["id"]))


@transactional
def materialize_pending_players(conn, tournament_id, pending_id=None):
    pending_players = list_pending_players(conn, tournament_id)
    if pending_id is not None:
        pending_players = [
            row for row in pending_players if row["id"] == pending_id]
    if not pending_players:
        return 0
    columns = table_columns(conn, "tournaments")
    select_sql = "SELECT pairing_system"
    if {"mm_bar", "mm_floor", "mm_zero"}.issubset(columns):
        select_sql += ", mm_bar, mm_floor, mm_zero"
    tournament = conn.execute(
        select_sql + " FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    created = 0
    materialized_ids = []
    for row in pending_players:
        display_name = row["display_name"]
        rating = row["rating"] or 0
        resolved_id = row["resolved_player_id"] if "resolved_player_id" in row.keys(
        ) else None
        if resolved_id is not None:
            player_id = int(resolved_id)
            resolved = conn.execute(
                "SELECT display_name, first_name, last_name FROM players_display WHERE id = ?", (player_id,)).fetchone()
            if resolved is None:
                raise ValueError("Resolved player not found")
            display_name = resolved["display_name"]
            conn.execute("UPDATE tournament_pending_players SET first_name = ?, last_name = ? WHERE tournament_id = ? AND id = ?",
                         (resolved["first_name"], resolved["last_name"], tournament_id, row["id"]))
        else:
            lookup = build_player_lookup(conn)
            if normalize_key(display_name) in lookup:
                raise ValueError("Player already exists; select an existing player")
            player_id = ensure_player(
                conn,
                display_name,
                rating=rating,
                initial_rating=rating,
                active=1,
                player_lookup=lookup,
                country=row["country"] if "country" in row.keys() else None,
            )
        conn.execute("UPDATE tournament_pairings SET white_player_id = ?, white_player_name = NULL WHERE round_id IN (SELECT id FROM tournament_rounds WHERE tournament_id = ?) AND white_player_id IS NULL AND white_player_name = ?", (player_id, tournament_id, display_name))
        conn.execute("UPDATE tournament_pairings SET black_player_id = ?, black_player_name = NULL WHERE round_id IN (SELECT id FROM tournament_rounds WHERE tournament_id = ?) AND black_player_id IS NULL AND black_player_name = ?", (player_id, tournament_id, display_name))
        initial_score = pending_initial_score(tournament, row["rank"])
        conn.execute("INSERT OR IGNORE INTO tournament_participants (tournament_id, player_id, seed_rating, seed_rank, category, initial_score, acceleration) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (tournament_id, player_id, rating, row["rank"] or 0, row["category"] or "", initial_score, 0))
        participation = row["participating"] if "participating" in row.keys(
        ) and row["participating"] else "1" * 20
        for round_row in conn.execute("SELECT id, round_number FROM tournament_rounds WHERE tournament_id = ?", (tournament_id,)).fetchall():
            pairing = conn.execute("SELECT is_bye FROM tournament_pairings WHERE round_id = ? AND (white_player_id = ? OR black_player_id = ?) LIMIT 1", (
                round_row["id"], player_id, player_id)).fetchone()
            status = ("bye" if pairing["is_bye"] else "paired") if pairing is not None else (
                "absent" if round_row["round_number"] <= len(participation) and participation[round_row["round_number"] - 1] == "0" else None)
            if status:
                conn.execute("INSERT OR REPLACE INTO tournament_round_players (round_id, player_id, status) VALUES (?, ?, ?)",
                             (round_row["id"], player_id, status))
                if status == "bye":
                    conn.execute(
                        "UPDATE tournament_participants SET received_bye = 1 WHERE tournament_id = ? AND player_id = ?", (tournament_id, player_id))
        created += 1
        materialized_ids.append(row["id"])
    if tournament and tournament["pairing_system"] == "mcmahon":
        recalculate_mcmahon_seeds(conn, tournament_id)
    if materialized_ids:
        conn.execute(f"DELETE FROM tournament_pending_players WHERE tournament_id = ? AND id IN ({', '.join('?' for _ in materialized_ids)})", (
            tournament_id, *materialized_ids))
    commit(conn)
    return created
