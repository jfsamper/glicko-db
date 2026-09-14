"""Compatibility facade for the decomposed tournament services."""
from services.tournament_lifecycle import delete_tournament
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
    _category_for_rating,
    list_pending_players as _list_pending_players, list_tournament_participants,
    materialize_pending_players as _materialize_pending_players,
    mcmahon_category_strength as _mcmahon_category_strength, player_lookup as _player_lookup,
    recalculate_mcmahon_seeds as _recalculate_mcmahon_seeds, suggest_player_name as _suggest_player_name,
)
from services.tournament_standings import get_tournament_standings
from services.tournament_status import (
    VALID_TOURNAMENT_RESULTS,
    _refresh_tournament_completion_state,
    _round_is_complete,
)

TOURNAMENT_STATUSES = ("draft", "active", "canceled", "completed")
