"""Tournament-to-match synchronization service boundary."""


def sync_match_pairing(conn, match_id, white_player_id, black_player_id, result, handicap_stones):
    from services.tournament_service import _legacy_sync_match_pairing

    return _legacy_sync_match_pairing(
        conn, match_id, white_player_id, black_player_id, result, handicap_stones
    )


def sync_pairing_match(conn, tournament_id, pairing_id):
    from services.tournament_service import _legacy_sync_pairing_match

    return _legacy_sync_pairing_match(conn, tournament_id, pairing_id)


def sync_tournament_matches(conn, tournament_id, name=None, match_date=None):
    from services.tournament_service import _legacy_sync_tournament_matches

    return _legacy_sync_tournament_matches(conn, tournament_id, name=name, match_date=match_date)


def save_tournament_matches(conn, tournament_id):
    from services.tournament_service import _legacy_save_tournament_matches

    return _legacy_save_tournament_matches(conn, tournament_id)


def process_tournament_round_matches(conn, tournament_id, round_id=None, match_date=None, event=None):
    from services.tournament_service import _legacy_process_tournament_round_matches

    return _legacy_process_tournament_round_matches(
        conn, tournament_id, round_id=round_id, match_date=match_date, event=event
    )
