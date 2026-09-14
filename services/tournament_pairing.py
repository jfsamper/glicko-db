"""Tournament pairing policy, orchestration, and round state."""
from collections import defaultdict
from config import GLICKO_K, GLICKO_M
from services.category_service import suggested_handicap_stones
from services.category_service import category_value
from services.pairing_service import DEFAULT_CATEGORY_ROUNDS, acceleration_for_rank, default_acceleration_rounds, effective_score_for_player, pair_players

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


def participant_state(conn, tournament_id, acceleration_scheme=None, acceleration_active=False):
    player_columns = table_columns(conn, "players")
    player_name = "p.display_name AS player_name" if "display_name" in player_columns else "CAST(tp.player_id AS TEXT) AS player_name"
    player_country = "COALESCE(p.country, '') AS player_country" if "country" in player_columns else "'' AS player_country"
    player_club = "COALESCE(p.club, '') AS player_club" if "club" in player_columns else "'' AS player_club"
    participants = conn.execute(f"SELECT tp.*, {player_name}, {player_country}, {player_club} FROM tournament_participants tp JOIN players p ON p.id = tp.player_id WHERE tp.tournament_id = ? ORDER BY tp.seed_rank, tp.id", (tournament_id,)).fetchall()
    previous = conn.execute("SELECT p.white_player_id, p.black_player_id, p.result, p.is_bye, p.handicap_stones, r.round_number FROM tournament_pairings p JOIN tournament_rounds r ON r.id = p.round_id WHERE r.tournament_id = ? ORDER BY r.round_number, p.board_number", (tournament_id,)).fetchall()
    columns = table_columns(conn, "tournaments"); settings_columns = ["bye_points", "absent_points", "pairing_system"]
    if "acceleration_rounds" in columns: settings_columns.append("acceleration_rounds")
    settings = conn.execute(f"SELECT {', '.join(settings_columns)} FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    bye_points = float(settings["bye_points"] if settings and settings["bye_points"] is not None else 1.0); absent_points = float(settings["absent_points"] if settings and settings["absent_points"] is not None else 0.0); pairing_system = settings["pairing_system"] if settings else "swiss"
    acceleration_rounds = int(settings["acceleration_rounds"] or 0) if settings and "acceleration_rounds" in settings.keys() else 1
    seed_order = sorted(participants, key=lambda row: (-float(row["seed_rating"] or 0), row["player_id"])); seed_ranks = {row["player_id"]: rank for rank, row in enumerate(seed_order, 1)}
    state = {row["player_id"]: {"id": row["player_id"], "name": row["player_name"], "rating": row["seed_rating"], "score": 0.0, "initial_score": row["initial_score"], "seed_acceleration": float(row["acceleration"] or 0), "acceleration": acceleration_for_rank(seed_ranks[row["player_id"]], len(participants), scheme=acceleration_scheme, player_rank=round(category_value(row["seed_rating"] or 1500))) if acceleration_active else 0.0, "category": row["category"], "country": row["player_country"], "club": row["player_club"], "opponents": set(), "colors": {"white": 0, "black": 0}, "received_bye": bool(row["received_bye"]), "draw_up_count": 0, "draw_down_count": 0} for row in participants}
    category_strength = {}
    for player in state.values(): category_strength[str(player["category"] or "")] = max(category_strength.get(str(player["category"] or ""), float("-inf")), category_value(player["rating"] or 1500))
    category_order = {category: order for order, (category, _) in enumerate(sorted(category_strength.items(), key=lambda item: (-item[1], item[0].casefold())))}
    for player in state.values(): player["category_order"] = category_order[str(player["category"] or "")]
    absent = conn.execute("SELECT rp.player_id, r.round_number FROM tournament_round_players rp JOIN tournament_rounds r ON r.id = rp.round_id WHERE r.tournament_id = ? AND rp.status = 'absent'", (tournament_id,)).fetchall()
    games_by_round = defaultdict(list); absences_by_round = defaultdict(list)
    for row in previous: games_by_round[row["round_number"]].append(row)
    for row in absent: absences_by_round[row["round_number"]].append(row["player_id"])
    def historical_score(player_id, round_number):
        player = state[player_id]; score = player["score"]
        if pairing_system == "mcmahon": score += float(player["initial_score"] or 0)
        elif pairing_system == "accelerated_swiss" and round_number <= acceleration_rounds: score += player["seed_acceleration"]
        return score
    for round_number in sorted(set(games_by_round) | set(absences_by_round)):
        for row in games_by_round[round_number]:
            white, black = row["white_player_id"], row["black_player_id"]
            if white not in state: continue
            if row["is_bye"] or black is None: state[white]["received_bye"] = True; state[white]["score"] += bye_points; continue
            if black not in state: continue
            white_score, black_score = historical_score(white, round_number), historical_score(black, round_number)
            if white_score < black_score: state[white]["draw_up_count"] += 1; state[black]["draw_down_count"] += 1
            elif black_score < white_score: state[black]["draw_up_count"] += 1; state[white]["draw_down_count"] += 1
            state[white]["opponents"].add(black); state[black]["opponents"].add(white)
            if not row["handicap_stones"]: state[white]["colors"]["white"] += 1; state[black]["colors"]["black"] += 1
            if row["result"] == "1-0": state[white]["score"] += 1.0
            elif row["result"] == "0-1": state[black]["score"] += 1.0
            elif row["result"] == "1/2-1/2": state[white]["score"] += 0.5; state[black]["score"] += 0.5
        for player_id in absences_by_round[round_number]:
            if player_id in state: state[player_id]["score"] += absent_points
    return state


def pairing_board_sort_key(pairing, state, pairing_system):
    if pairing["is_bye"]: return (1, 0.0, 0.0, "", 0)
    players = [state[player_id] for player_id in (pairing["white_player_id"], pairing["black_player_id"]) if player_id in state]
    if not players: return (0, 0.0, 0.0, "", 0)
    strongest = min(players, key=lambda player: (-effective_score_for_player(player, pairing_system), -float(player.get("rating", 0) or 0), str(player.get("name", player["id"])).casefold(), player["id"]))
    return (0, -effective_score_for_player(strongest, pairing_system), -float(strongest.get("rating", 0) or 0), str(strongest.get("name", strongest["id"])).casefold(), strongest["id"])


def reorder_round_boards(conn, tournament_id, round_id):
    tournament = conn.execute("SELECT pairing_system FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    if tournament is None: raise ValueError("Tournament not found")
    pairings = conn.execute("SELECT id, is_bye, white_player_id, black_player_id FROM tournament_pairings WHERE round_id = ?", (round_id,)).fetchall()
    if len(pairings) < 2: return
    state = participant_state(conn, tournament_id); ordered = sorted(pairings, key=lambda pairing: pairing_board_sort_key(pairing, state, tournament["pairing_system"]))
    highest = conn.execute("SELECT COALESCE(MAX(board_number), 0) FROM tournament_pairings WHERE round_id = ?", (round_id,)).fetchone()[0]
    for offset, pairing in enumerate(pairings, 1): conn.execute("UPDATE tournament_pairings SET board_number = ? WHERE id = ?", (highest + offset, pairing["id"]))
    for board_number, pairing in enumerate(ordered, 1): conn.execute("UPDATE tournament_pairings SET board_number = ? WHERE id = ?", (board_number, pairing["id"]))


def generate_next_round(conn, tournament_id):
    from services.tournament_status import _refresh_tournament_completion_state
    tournament = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    if tournament is None: raise ValueError("Tournament not found")
    round_number = conn.execute("SELECT COALESCE(MAX(round_number), 0) + 1 FROM tournament_rounds WHERE tournament_id = ?", (tournament_id,)).fetchone()[0]
    if tournament["rounds"] and round_number > tournament["rounds"]: raise ValueError("All tournament rounds have already been generated")
    policy = pairing_policy(tournament, round_number)
    state = participant_state(conn, tournament_id, acceleration_scheme=tournament["acceleration_scheme"] if "acceleration_scheme" in tournament.keys() else None, acceleration_active=policy["acceleration_active"])
    if len(state) < 2: raise ValueError("At least two tournament players are required")
    pairings = pair_players(list(state.values()), tournament["pairing_system"], category_strict=policy["category_strict"])
    pairings.sort(key=lambda pairing: pairing_board_sort_key(pairing, state, tournament["pairing_system"]))
    conn.execute("INSERT INTO tournament_rounds (tournament_id, round_number, status) VALUES (?, ?, 'scheduled')", (tournament_id, round_number)); round_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for board_number, pairing in enumerate(pairings, 1):
        handicap = 0 if pairing["is_bye"] else auto_handicap_stones(conn, pairing["white_player_id"], pairing["black_player_id"])
        conn.execute("INSERT INTO tournament_pairings (round_id, board_number, white_player_id, black_player_id, is_bye, handicap_stones) VALUES (?, ?, ?, ?, ?, ?)", (round_id, board_number, pairing["white_player_id"], pairing["black_player_id"], int(pairing["is_bye"]), handicap))
        if pairing["is_bye"]:
            conn.execute("INSERT OR REPLACE INTO tournament_round_players (round_id, player_id, status) VALUES (?, ?, 'bye')", (round_id, pairing["white_player_id"])); conn.execute("UPDATE tournament_participants SET received_bye = 1 WHERE tournament_id = ? AND player_id = ?", (tournament_id, pairing["white_player_id"]))
        else:
            conn.executemany("INSERT OR REPLACE INTO tournament_round_players (round_id, player_id, status) VALUES (?, ?, 'paired')", [(round_id, pairing["white_player_id"]), (round_id, pairing["black_player_id"])])
    conn.execute("UPDATE tournaments SET status = 'active' WHERE id = ?", (tournament_id,)); _refresh_tournament_completion_state(conn, tournament_id, round_id); conn.commit(); return round_id, pairings


def set_round_player_status(conn, tournament_id, round_id, player_id, status):
    if status != "bye": raise ValueError("Invalid round player status")
    if conn.execute("SELECT 1 FROM tournament_rounds WHERE id = ? AND tournament_id = ?", (round_id, tournament_id)).fetchone() is None or conn.execute("SELECT 1 FROM tournament_participants WHERE tournament_id = ? AND player_id = ?", (tournament_id, player_id)).fetchone() is None: raise ValueError("Round player not found")
    if conn.execute("SELECT 1 FROM tournament_pairings WHERE round_id = ? AND (white_player_id = ? OR black_player_id = ?)", (round_id, player_id, player_id)).fetchone(): raise ValueError("Player is already paired in this round")
    if conn.execute("SELECT 1 FROM tournament_round_players WHERE round_id = ? AND player_id = ?", (round_id, player_id)).fetchone(): raise ValueError("Player already has a status in this round")
    conn.execute("INSERT INTO tournament_round_players (round_id, player_id, status) VALUES (?, ?, ?)", (round_id, player_id, status)); conn.execute("INSERT INTO tournament_pairings (round_id, board_number, white_player_id, black_player_id, is_bye) VALUES (?, (SELECT COALESCE(MAX(board_number), 0) + 1 FROM tournament_pairings WHERE round_id = ?), ?, NULL, 1)", (round_id, round_id, player_id)); conn.execute("UPDATE tournament_participants SET received_bye = 1 WHERE tournament_id = ? AND player_id = ?", (tournament_id, player_id)); reorder_round_boards(conn, tournament_id, round_id); conn.commit()


def pair_selected_players(conn, tournament_id, round_id, player_ids):
    selected = list(dict.fromkeys(int(player_id) for player_id in player_ids if player_id not in (None, "")))
    if selected:
        marked = {row[0] for row in conn.execute("SELECT player_id FROM tournament_round_players WHERE round_id = ? AND player_id IN ({})".format(",".join("?" for _ in selected)), (round_id, *selected)).fetchall()}; selected = [player_id for player_id in selected if player_id not in marked]
    if not selected:
        selected = [row["player_id"] for row in conn.execute("SELECT tp.player_id FROM tournament_participants tp LEFT JOIN tournament_pairings p ON p.round_id = ? AND (p.white_player_id = tp.player_id OR p.black_player_id = tp.player_id) LEFT JOIN tournament_round_players rrp ON rrp.round_id = ? AND rrp.player_id = tp.player_id WHERE tp.tournament_id = ? AND p.id IS NULL AND rrp.player_id IS NULL ORDER BY tp.seed_rank, tp.player_id", (round_id, round_id, tournament_id)).fetchall()]
    if not selected: return
    if len(selected) == 1: return set_round_player_status(conn, tournament_id, round_id, selected[0], "bye")
    tournament = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone(); current_round = conn.execute("SELECT round_number FROM tournament_rounds WHERE id = ?", (round_id,)).fetchone()[0]; policy = pairing_policy(tournament, current_round); state = participant_state(conn, tournament_id, acceleration_scheme=tournament["acceleration_scheme"] if "acceleration_scheme" in tournament.keys() else None, acceleration_active=policy["acceleration_active"]); pairings = pair_players([state[player_id] for player_id in selected if player_id in state], tournament["pairing_system"], category_strict=policy["category_strict"])
    for pairing in pairings:
        if pairing["is_bye"]: set_round_player_status(conn, tournament_id, round_id, pairing["white_player_id"], "bye")
        else: manual_pair(conn, tournament_id, round_id, pairing["white_player_id"], pairing["black_player_id"])


def add_participant(conn, tournament_id, player_id):
    from services.tournament_participants import recalculate_mcmahon_seeds
    columns = table_columns(conn, "tournaments")
    select_sql = "SELECT pairing_system, tournament_type"
    if "acceleration_scheme" in columns: select_sql += ", acceleration_scheme"
    if {"mm_bar", "mm_floor", "mm_zero"}.issubset(columns): select_sql += ", mm_bar, mm_floor, mm_zero"
    tournament = conn.execute(select_sql + " FROM tournaments WHERE id = ?", (tournament_id,)).fetchone(); player = conn.execute("SELECT id, rating FROM players WHERE id = ? AND active = 1", (player_id,)).fetchone()
    if tournament is None or player is None: raise ValueError("Tournament player not found")
    if conn.execute("SELECT 1 FROM tournament_participants WHERE tournament_id = ? AND player_id = ?", (tournament_id, player_id)).fetchone(): raise ValueError("Player is already in this tournament")
    count = conn.execute("SELECT COUNT(*) FROM tournament_participants WHERE tournament_id = ?", (tournament_id,)).fetchone()[0]
    if tournament["pairing_system"] == "mcmahon":
        from services.category_service import glicko_to_category
        category = glicko_to_category(player["rating"])
        participant_columns = table_columns(conn, "tournament_participants")
        if {"mm_bar", "mm_floor", "mm_zero"}.issubset(columns):
            count += 1
            mm_zero = int(tournament["mm_zero"] or 0)
            if mm_zero < count: conn.execute("UPDATE tournaments SET mm_zero = ? WHERE id = ?", (count, tournament_id))
        fields = "mc_seeds_calculated" if "mc_seeds_calculated" in participant_columns else None
        if fields:
            conn.execute("INSERT INTO tournament_participants (tournament_id, player_id, seed_rating, category, initial_score, acceleration, mc_seeds_calculated) VALUES (?, ?, ?, ?, 0, 0, 0)", (tournament_id, player_id, player["rating"] or 0, category))
        else:
            conn.execute("INSERT INTO tournament_participants (tournament_id, player_id, seed_rating, category, initial_score, acceleration) VALUES (?, ?, ?, ?, 0, 0)", (tournament_id, player_id, player["rating"] or 0, category))
        recalculate_mcmahon_seeds(conn, tournament_id); conn.commit(); return
    conn.execute("INSERT INTO tournament_participants (tournament_id, player_id, seed_rating, seed_rank, category, initial_score, acceleration) VALUES (?, ?, ?, ?, ?, ?, ?)", (tournament_id, player_id, player["rating"] or 0, count + 1, "", 0.0, 0.0)); conn.commit()


def remove_participant(conn, tournament_id, player_id):
    tournament = conn.execute("SELECT pairing_system FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    if conn.execute("SELECT 1 FROM tournament_pairings p JOIN tournament_rounds r ON r.id = p.round_id WHERE r.tournament_id = ? AND (p.white_player_id = ? OR p.black_player_id = ?)", (tournament_id, player_id, player_id)).fetchone(): raise ValueError("Cannot remove a player after they have been paired")
    if not conn.execute("DELETE FROM tournament_participants WHERE tournament_id = ? AND player_id = ?", (tournament_id, player_id)).rowcount: raise ValueError("Tournament player not found")
    if tournament and tournament["pairing_system"] == "mcmahon": from services.tournament_participants import recalculate_mcmahon_seeds; recalculate_mcmahon_seeds(conn, tournament_id)
    conn.commit()


def manual_pair(conn, tournament_id, round_id, white_player_id, black_player_id, handicap_stones=None):
    if conn.execute("SELECT 1 FROM tournament_rounds WHERE id = ? AND tournament_id = ?", (round_id, tournament_id)).fetchone() is None or white_player_id == black_player_id: raise ValueError("Invalid manual pairing")
    ids = {row[0] for row in conn.execute("SELECT player_id FROM tournament_participants WHERE tournament_id = ?", (tournament_id,)).fetchall()}
    if white_player_id not in ids or black_player_id not in ids: raise ValueError("Both players must be in the tournament")
    if conn.execute("SELECT 1 FROM tournament_round_players WHERE round_id = ? AND player_id IN (?, ?)", (round_id, white_player_id, black_player_id)).fetchone() or conn.execute("SELECT 1 FROM tournament_pairings WHERE round_id = ? AND (white_player_id IN (?, ?) OR black_player_id IN (?, ?))", (round_id, white_player_id, black_player_id, white_player_id, black_player_id)).fetchone(): raise ValueError("A player is already paired in this round")
    used = {row[0] for row in conn.execute("SELECT board_number FROM tournament_pairings WHERE round_id = ?", (round_id,)).fetchall()}; board = 1
    while board in used: board += 1
    if handicap_stones is None and tournament_handicap_enabled(conn, tournament_id): handicap_stones = auto_handicap_stones(conn, white_player_id, black_player_id)
    conn.execute("INSERT INTO tournament_pairings (round_id, board_number, white_player_id, black_player_id, is_bye, handicap_stones) VALUES (?, ?, ?, ?, 0, ?)", (round_id, board, white_player_id, black_player_id, handicap_stones or 0)); reorder_round_boards(conn, tournament_id, round_id); conn.commit()


def update_pairing(conn, tournament_id, pairing_id, white_player_id, black_player_id):
    from services.tournament_matches import sync_pairing_match
    from services.tournament_status import _refresh_tournament_completion_state
    pairing = conn.execute("SELECT p.round_id, p.white_player_id AS old_white_player_id, p.black_player_id AS old_black_player_id FROM tournament_pairings p JOIN tournament_rounds r ON r.id = p.round_id WHERE p.id = ? AND r.tournament_id = ? AND p.is_bye = 0", (pairing_id, tournament_id)).fetchone()
    if pairing is None or white_player_id == black_player_id: raise ValueError("Invalid pairing")
    ids = {row[0] for row in conn.execute("SELECT player_id FROM tournament_participants WHERE tournament_id = ?", (tournament_id,)).fetchall()}
    if white_player_id not in ids or black_player_id not in ids: raise ValueError("Both players must be in the tournament")
    if conn.execute("SELECT 1 FROM tournament_pairings WHERE round_id = ? AND id != ? AND (white_player_id IN (?, ?) OR black_player_id IN (?, ?))", (pairing["round_id"], pairing_id, white_player_id, black_player_id, white_player_id, black_player_id)).fetchone(): raise ValueError("A player is already paired in this round")
    conn.execute("UPDATE tournament_pairings SET white_player_id = ?, black_player_id = ? WHERE id = ?", (white_player_id, black_player_id, pairing_id)); conn.execute("DELETE FROM tournament_round_players WHERE round_id = ? AND player_id IN (?, ?)", (pairing["round_id"], pairing["old_white_player_id"], pairing["old_black_player_id"])); conn.executemany("INSERT OR REPLACE INTO tournament_round_players (round_id, player_id, status) VALUES (?, ?, 'paired')", [(pairing["round_id"], white_player_id), (pairing["round_id"], black_player_id)]); sync_pairing_match(conn, tournament_id, pairing_id); _refresh_tournament_completion_state(conn, tournament_id, pairing["round_id"]); conn.commit()


def update_pairing_handicap(conn, tournament_id, pairing_id, handicap_stones):
    from services.tournament_matches import sync_pairing_match
    if handicap_stones is None: raise ValueError("handicap_stones is required")
    try: handicap_stones = int(handicap_stones)
    except (TypeError, ValueError): raise ValueError("handicap_stones must be an integer")
    if not 0 <= handicap_stones <= 9: raise ValueError("handicap_stones must be between 0 and 9")
    updated = conn.execute("UPDATE tournament_pairings SET handicap_stones = ? WHERE id = ? AND round_id IN (SELECT id FROM tournament_rounds WHERE tournament_id = ?)", (handicap_stones, pairing_id, tournament_id)).rowcount
    if not updated: raise ValueError("Pairing not found")
    sync_pairing_match(conn, tournament_id, pairing_id); conn.commit()


def unpair(conn, tournament_id, pairing_id):
    from services.reporting_service import ensure_tournament_match_identity
    from services.tournament_status import _refresh_tournament_completion_state
    pairing = conn.execute("SELECT round_id, white_player_id, black_player_id FROM tournament_pairings WHERE id = ?", (pairing_id,)).fetchone()
    deleted = conn.execute("DELETE FROM tournament_pairings WHERE id = ? AND round_id IN (SELECT id FROM tournament_rounds WHERE tournament_id = ?)", (pairing_id, tournament_id)).rowcount
    if not deleted: raise ValueError("Pairing not found")
    if pairing:
        conn.execute("DELETE FROM tournament_round_players WHERE round_id = ? AND player_id IN (?, ?)", (pairing["round_id"], pairing["white_player_id"], pairing["black_player_id"])); ensure_tournament_match_identity(conn); conn.execute("DELETE FROM matches WHERE tournament_pairing_id = ?", (pairing_id,)); _refresh_tournament_completion_state(conn, tournament_id, pairing["round_id"])
    conn.commit()


def unpair_all(conn, tournament_id, round_id):
    if conn.execute("SELECT id FROM tournament_rounds WHERE id = ? AND tournament_id = ?", (round_id, tournament_id)).fetchone() is None: raise ValueError("Round not found")
    pairing_ids = [row["id"] for row in conn.execute("SELECT id FROM tournament_pairings WHERE round_id = ? ORDER BY id", (round_id,)).fetchall()]
    for pairing_id in pairing_ids: unpair(conn, tournament_id, pairing_id)
    return len(pairing_ids)
