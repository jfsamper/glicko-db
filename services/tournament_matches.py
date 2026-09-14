"""Tournament-to-match synchronization service."""


def sync_match_pairing(conn, match_id, white_player_id, black_player_id, result, handicap_stones):
    from services.reporting_service import ensure_tournament_match_identity
    from services.tournament_status import VALID_TOURNAMENT_RESULTS, _refresh_tournament_completion_state
    ensure_tournament_match_identity(conn)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('tournament_pairings', 'tournament_rounds')").fetchall()}
    if len(tables) != 2: return None
    pairing = conn.execute("SELECT p.id, p.round_id, r.tournament_id FROM matches m JOIN tournament_pairings p ON p.id = m.tournament_pairing_id JOIN tournament_rounds r ON r.id = p.round_id WHERE m.id = ?", (match_id,)).fetchone()
    if pairing is None: return None
    if result not in VALID_TOURNAMENT_RESULTS: raise ValueError("Invalid tournament result")
    participant_ids = {row[0] for row in conn.execute("SELECT player_id FROM tournament_participants WHERE tournament_id = ?", (pairing["tournament_id"],)).fetchall()}
    if white_player_id == black_player_id or white_player_id not in participant_ids or black_player_id not in participant_ids: raise ValueError("Both players must be in the tournament")
    occupied = conn.execute("SELECT 1 FROM tournament_pairings WHERE round_id = ? AND id != ? AND (white_player_id IN (?, ?) OR black_player_id IN (?, ?))", (pairing["round_id"], pairing["id"], white_player_id, black_player_id, white_player_id, black_player_id)).fetchone()
    if occupied: raise ValueError("A player is already paired in this round")
    conn.execute("UPDATE tournament_pairings SET white_player_id = ?, black_player_id = ?, result = ?, handicap_stones = ? WHERE id = ?", (white_player_id, black_player_id, result, handicap_stones or 0, pairing["id"]))
    _refresh_tournament_completion_state(conn, pairing["tournament_id"], pairing["round_id"])
    return pairing["tournament_id"]


def sync_pairing_match(conn, tournament_id, pairing_id):
    from services.reporting_service import ensure_tournament_match_identity
    from services.sgf_service import ensure_sgf_schema
    from services.timezone_service import current_date
    from services.tournament_status import PLAYED_GAME_RESULTS
    ensure_tournament_match_identity(conn); ensure_sgf_schema(conn)

    pairing = conn.execute("SELECT p.id, p.white_player_id, p.black_player_id, p.result, p.is_bye, p.handicap_stones, r.round_number, t.name, t.location, t.begin_date, t.end_date FROM tournament_pairings p JOIN tournament_rounds r ON r.id = p.round_id JOIN tournaments t ON t.id = r.tournament_id WHERE p.id = ? AND r.tournament_id = ?", (pairing_id, tournament_id)).fetchone()
    if pairing is None: raise ValueError("Pairing not found")
    existing = conn.execute("SELECT id, match_date, event, location, notes, round_number FROM matches WHERE tournament_pairing_id = ?", (pairing_id,)).fetchone()
    # Absent/default results (!0-1, 1-!0, !0-0) never produce a match row.
    valid_game = not pairing["is_bye"] and pairing["white_player_id"] is not None and pairing["black_player_id"] is not None and pairing["result"] in PLAYED_GAME_RESULTS
    if not valid_game:
        if existing is not None: conn.execute("DELETE FROM matches WHERE id = ?", (existing["id"],))
        return False
    match_date = existing["match_date"] if existing else pairing["begin_date"] or pairing["end_date"] or current_date().isoformat()
    event = existing["event"] if existing and existing["event"] else pairing["name"]
    notes = existing["notes"] if existing and existing["notes"] is not None else str(pairing["round_number"])
    match_round = existing["round_number"] if existing and existing["round_number"] is not None else pairing["round_number"]
    handicap = pairing["handicap_stones"] or 0
    values = (match_date, pairing["white_player_id"], pairing["black_player_id"], pairing["result"], event, pairing["location"], notes, match_round, pairing_id, handicap)
    if existing is None:
        conn.execute("INSERT INTO matches (match_date, white_player_id, black_player_id, result, event, location, notes, round_number, tournament_pairing_id, handicap_stones) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
    else:
        conn.execute("UPDATE matches SET match_date = ?, white_player_id = ?, black_player_id = ?, result = ?, event = ?, notes = ?, round_number = ?, handicap_stones = ?, location = ? WHERE id = ?", (match_date, pairing["white_player_id"], pairing["black_player_id"], pairing["result"], event, notes, match_round, handicap, pairing["location"], existing["id"]))
    return True


def sync_tournament_matches(conn, tournament_id, name=None, match_date=None):
    from services.reporting_service import ensure_tournament_match_identity

    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'matches'").fetchone() is None:
        return 0
    ensure_tournament_match_identity(conn)
    fields = []
    values = []
    if name is not None:
        fields.append("event = ?")
        values.append(name)
    if match_date:
        fields.append("match_date = ?")
        values.append(match_date)
    if not fields:
        return 0
    values.append(tournament_id)
    return conn.execute(
        f"""
        UPDATE matches AS m SET {', '.join(fields)}
        WHERE m.tournament_pairing_id IN (
            SELECT p.id FROM tournament_pairings p
            JOIN tournament_rounds r ON r.id = p.round_id
            WHERE r.tournament_id = ?
        )
        """,
        values,
    ).rowcount


def save_tournament_matches(conn, tournament_id):
    from services.tournament_status import _refresh_tournament_completion_state

    if conn.execute("SELECT id FROM tournaments WHERE id = ?", (tournament_id,)).fetchone() is None:
        raise ValueError("Tournament not found")
    pairing_ids = conn.execute(
        """
        SELECT p.id FROM tournament_pairings p
        JOIN tournament_rounds r ON r.id = p.round_id
        WHERE r.tournament_id = ? ORDER BY r.round_number, p.board_number
        """,
        (tournament_id,),
    ).fetchall()
    saved = sum(
        bool(sync_pairing_match(conn, tournament_id, pairing["id"]))
        for pairing in pairing_ids
    )
    _refresh_tournament_completion_state(conn, tournament_id)
    return saved


def process_tournament_round_matches(conn, tournament_id, round_id=None, match_date=None, event=None):
    from services.timezone_service import current_date
    from services.reporting_service import ensure_tournament_match_identity
    from services.tournament_participants import materialize_pending_players
    from services.tournament_status import PLAYED_GAME_RESULTS, _refresh_tournament_completion_state

    ensure_tournament_match_identity(conn)
    if round_id is None:
        row = conn.execute("SELECT id FROM tournament_rounds WHERE tournament_id = ? ORDER BY round_number DESC LIMIT 1", (tournament_id,)).fetchone()
        if row is None: raise ValueError("Round not found")
        round_id = row[0]
    round_row = conn.execute("SELECT id, round_number FROM tournament_rounds WHERE id = ? AND tournament_id = ?", (round_id, tournament_id)).fetchone()
    if round_row is None: raise ValueError("Round not found")
    if match_date is None:
        row = conn.execute("SELECT COALESCE(begin_date, end_date, ?) FROM tournaments WHERE id = ?", (current_date().isoformat(), tournament_id)).fetchone()
        match_date = row[0] if row else current_date().isoformat()
    tournament = conn.execute("SELECT name, rounds FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    tournament_name = tournament["name"] if tournament else ""
    rounds_limit = tournament["rounds"] if tournament else None
    event_name = event or (f"{tournament_name} Round {round_row['round_number']}" if tournament_name else "Tournament round")
    inserted = 0
    materialize_pending_players(conn, tournament_id)
    pairings = conn.execute("SELECT id, white_player_id, black_player_id, result, handicap_stones FROM tournament_pairings WHERE round_id = ? AND is_bye = 0 AND result IS NOT NULL AND result != ''", (round_id,)).fetchall()
    for pairing in pairings:
        if pairing["white_player_id"] is None or pairing["black_player_id"] is None or pairing["result"] not in PLAYED_GAME_RESULTS:
            # Absent/default result: never materialize (and remove any stale match row).
            conn.execute("DELETE FROM matches WHERE tournament_pairing_id = ?", (pairing["id"],))
            continue
        if round_row["round_number"] <= 0 or (rounds_limit is not None and round_row["round_number"] > rounds_limit): continue
        handicap = pairing["handicap_stones"] if "handicap_stones" in pairing.keys() and pairing["handicap_stones"] is not None else 0
        existing = conn.execute("SELECT id, match_date, result, event, handicap_stones FROM matches WHERE tournament_pairing_id = ?", (pairing["id"],)).fetchone()
        expected = (match_date, pairing["result"], event_name, handicap)
        current = (existing["match_date"], existing["result"], existing["event"], existing["handicap_stones"] or 0) if existing else None
        if existing is not None:
            if current != expected:
                conn.execute("UPDATE matches SET match_date = ?, result = ?, event = ?, notes = ?, round_number = ?, handicap_stones = ? WHERE id = ?", (match_date, pairing["result"], event_name, round_row["round_number"], round_row["round_number"], handicap, existing["id"]))
            continue
        conn.execute("INSERT INTO matches (match_date, white_player_id, black_player_id, result, event, notes, round_number, tournament_pairing_id, handicap_stones) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (match_date, pairing["white_player_id"], pairing["black_player_id"], pairing["result"], event_name, round_row["round_number"], round_row["round_number"], pairing["id"], handicap))
        inserted += 1
    _refresh_tournament_completion_state(conn, tournament_id, round_id)
    return inserted


def set_pairing_result(conn, tournament_id, pairing_id, result):
    from services.tournament_status import VALID_TOURNAMENT_RESULTS, _refresh_tournament_completion_state

    pairing = conn.execute(
        """
        SELECT p.id, p.is_bye, p.round_id FROM tournament_pairings p
        JOIN tournament_rounds r ON r.id = p.round_id
        WHERE p.id = ? AND r.tournament_id = ?
        """,
        (pairing_id, tournament_id),
    ).fetchone()
    if pairing is None:
        raise ValueError("Pairing not found")
    if pairing["is_bye"] and result not in {"", None}:
        raise ValueError("Bye pairings cannot have a result")
    if result not in {"", None, *sorted(VALID_TOURNAMENT_RESULTS)}:
        raise ValueError("Invalid tournament result")
    updated = conn.execute(
        "UPDATE tournament_pairings SET result = ? WHERE id = ? AND round_id IN (SELECT id FROM tournament_rounds WHERE tournament_id = ?)",
        (result or None, pairing_id, tournament_id),
    ).rowcount
    if not updated:
        raise ValueError("Pairing not found")
    sync_pairing_match(conn, tournament_id, pairing_id)
    _refresh_tournament_completion_state(conn, tournament_id, pairing["round_id"])
    conn.commit()
