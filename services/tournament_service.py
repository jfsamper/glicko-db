"""Compatibility facade for the decomposed tournament services."""
from config import GLICKO_K, GLICKO_M
from services.category_service import glicko_to_category
from services.tournament_gotha import create_tournament_from_gotha, export_tournament_results, read_gotha_tournament
from services.tournament_matches import process_tournament_round_matches, save_tournament_matches, set_pairing_result, sync_match_pairing, sync_pairing_match, sync_tournament_matches
from services.tournament_pairing import (
    SUPPORTED_SYSTEMS, add_participant, auto_handicap_stones as _auto_handicap_stones,
    generate_next_round, manual_pair, normalize_tournament_rounds, normalize_tournament_system,
    pairing_board_sort_key as _pairing_board_sort_key, pairing_policy as _pairing_policy,
    participant_state as _participant_state, pair_selected_players, remove_participant,
    reorder_round_boards as _reorder_round_boards, set_round_player_status,
    table_columns as _table_columns, tournament_handicap_enabled as _tournament_handicap_enabled,
    unpair, unpair_all, update_pairing, update_pairing_handicap, update_tournament_handicaps,
)
from services.tournament_participants import (
    list_pending_players as _list_pending_players, list_tournament_participants,
    materialize_pending_players as _materialize_pending_players,
    mcmahon_category_strength as _mcmahon_category_strength, player_lookup as _player_lookup,
    recalculate_mcmahon_seeds as _recalculate_mcmahon_seeds, suggest_player_name as _suggest_player_name,
)
from services.tournament_standings import get_tournament_standings

TOURNAMENT_STATUSES = ("draft", "active", "canceled", "completed")
VALID_TOURNAMENT_RESULTS = {"1-0", "0-1", "1/2-1/2", "!0-1", "1-!0", "!0-0"}


def _category_for_rating(conn, rating):
    config = conn.execute("SELECT glicko_k, glicko_m FROM category_config WHERE id = 1").fetchone() if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'category_config'").fetchone() else None
    category = glicko_to_category(rating, k=config["glicko_k"] if config else GLICKO_K, m=config["glicko_m"] if config else GLICKO_M)
    if category.endswith(" dan"): return f"{category[:-4]}D"
    if category.endswith(" kyu"): return f"{category[:-4]}K"
    return category


def _round_is_complete(conn, round_id):
    total = conn.execute("SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ? AND is_bye = 0", (round_id,)).fetchone()[0]
    if total == 0: return True
    placeholders = ", ".join("?" for _ in VALID_TOURNAMENT_RESULTS)
    completed = conn.execute(f"SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ? AND is_bye = 0 AND result IN ({placeholders})", (round_id, *sorted(VALID_TOURNAMENT_RESULTS))).fetchone()[0]
    return completed == total


def _refresh_tournament_completion_state(conn, tournament_id, round_id=None):
    tournament = conn.execute("SELECT rounds FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    if tournament is None: return
    if round_id is None:
        row = conn.execute("SELECT id FROM tournament_rounds WHERE tournament_id = ? ORDER BY round_number DESC LIMIT 1", (tournament_id,)).fetchone()
        if row is None:
            conn.execute("UPDATE tournaments SET status = 'draft' WHERE id = ?", (tournament_id,)); conn.commit(); return
        round_id = row[0]
    if conn.execute("SELECT round_number FROM tournament_rounds WHERE id = ? AND tournament_id = ?", (round_id, tournament_id)).fetchone() is None: return
    placeholders = ", ".join("?" for _ in VALID_TOURNAMENT_RESULTS)
    played = conn.execute(f"SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ? AND is_bye = 0 AND result IN ({placeholders})", (round_id, *sorted(VALID_TOURNAMENT_RESULTS))).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ? AND is_bye = 0", (round_id,)).fetchone()[0]
    if total > 0: conn.execute("UPDATE tournament_rounds SET status = ? WHERE id = ?", ("completed" if played == total else "scheduled", round_id))
    rounds = conn.execute("SELECT id, round_number, status FROM tournament_rounds WHERE tournament_id = ? ORDER BY round_number", (tournament_id,)).fetchall()
    if not rounds:
        conn.execute("UPDATE tournaments SET status = 'draft' WHERE id = ?", (tournament_id,)); conn.commit(); return
    completed_rounds = sum(row["status"] == "completed" or _round_is_complete(conn, row["id"]) for row in rounds)
    final_round = max(row["round_number"] for row in rounds); limit = int(tournament["rounds"] or 0)
    conn.execute("UPDATE tournaments SET status = ? WHERE id = ?", ("completed" if limit and final_round >= limit and completed_rounds == len(rounds) else "active", tournament_id)); conn.commit()


def delete_tournament(conn, tournament_id):
    from app import repair_legacy_players_table
    repair_legacy_players_table(conn)
    if conn.execute("SELECT id FROM tournaments WHERE id = ?", (tournament_id,)).fetchone() is None: raise ValueError("Tournament not found")
    try:
        conn.execute("DELETE FROM tournament_pairings WHERE round_id IN (SELECT id FROM tournament_rounds WHERE tournament_id = ?)", (tournament_id,))
        conn.execute("DELETE FROM tournament_round_players WHERE round_id IN (SELECT id FROM tournament_rounds WHERE tournament_id = ?)", (tournament_id,))
        conn.execute("DELETE FROM tournament_rounds WHERE tournament_id = ?", (tournament_id,))
        conn.execute("DELETE FROM tournament_participants WHERE tournament_id = ?", (tournament_id,))
        conn.execute("DELETE FROM tournament_pending_players WHERE tournament_id = ?", (tournament_id,))
        conn.execute("DELETE FROM tournaments WHERE id = ?", (tournament_id,)); conn.commit()
    except Exception:
        conn.rollback(); raise
