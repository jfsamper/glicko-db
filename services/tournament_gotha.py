"""OpenGotha service boundary for tournament metadata and XML operations."""
from pathlib import Path
import xml.etree.ElementTree as ET

from config import DEFAULT_RATING, GLICKO_M
from services.helpers import normalize_key, normalize_text
from services.import_gotha import GothaPlayer, GothaTournamentPayload
from services.pairing_service import DEFAULT_ACCELERATION_SCHEME, DEFAULT_CATEGORY_ROUNDS, default_acceleration_rounds


def _resolve_gotha_path(xml_path):
    path = Path(xml_path)
    if path.exists():
        return path
    alternatives = (
        path.with_name(path.name.replace(" ", "_")),
        path.with_name(path.name.replace("_", " ")),
    )
    for alternative in alternatives:
        if alternative.exists():
            return alternative
    return path


def _rank_value(value, default=0):
    text = str(value or "").strip().upper()
    if text.endswith("D"):
        return int(text[:-1]) - 1
    if text.endswith("K"):
        return -int(text[:-1])
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _mms_offset(value, default=0):
    text = str(value or "").strip().upper()
    if not text:
        return default
    if text.endswith(("D", "K")):
        try:
            return int(text[:-1])
        except ValueError:
            return default
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


def read_gotha_tournament(xml_path: str | Path, pairing_system: str | None = None) -> GothaTournamentPayload:
    """Read OpenGotha XML and return a typed tournament metadata payload."""
    xml_path = _resolve_gotha_path(xml_path)
    root = ET.parse(xml_path).getroot()
    tournament_root = root.find("TournamentParameterSet") if root.tag != "TournamentParameterSet" else root
    if tournament_root is None:
        tournament_root = root
    general = tournament_root.find("GeneralParameterSet")
    if general is None:
        general = root.find("GeneralParameterSet")
    if general is None:
        raise ValueError("OpenGotha tournament metadata is missing")

    players_container = tournament_root.find("Players")
    if players_container is None:
        players_container = root.find("Players")
    players = []
    for player in players_container.findall("Player") if players_container is not None else []:
        first_name = player.get("firstName", "").strip()
        last_name = player.get("name", "").strip()
        try:
            rating = float(player.get("rating") or DEFAULT_RATING)
        except (TypeError, ValueError):
            rating = DEFAULT_RATING
        rating = max(rating, float(GLICKO_M))
        players.append(GothaPlayer(
            key=normalize_key(f"{last_name}{first_name}"),
            display_name=f"{first_name} {last_name}".strip(),
            rating=rating,
            rank=0,
            rank_value=_rank_value(player.get("rank") or player.get("grade")),
            category=player.get("grade", ""),
            country=player.get("country", ""),
            club=player.get("club", ""),
            participating=player.get("participating") or "1" * 20,
        ))
    players.sort(key=lambda player: (-player.rank_value, -player.rating, player.display_name.casefold()))
    for rank, player in enumerate(players, 1):
        player.rank = rank

    pairing_parameters = tournament_root.find("PairingParameterSet")
    if pairing_parameters is None:
        pairing_parameters = root.find("PairingParameterSet")
    placement_criteria = tournament_root.findall("PlacementParameterSet/PlacementCriteria/PlacementCriterion")
    placement_criteria = placement_criteria or root.findall("PlacementParameterSet/PlacementCriteria/PlacementCriterion")
    placement_names = [(criterion.get("name") or "").strip().upper().replace(" ", "") for criterion in placement_criteria]
    explicit = (pairing_system or "").strip().lower()
    if explicit in {"mcmahon", "mc-mahon"}:
        pairing_system = "mcmahon"
    elif explicit == "swiss_cat":
        pairing_system = "swiss_cat"
    elif explicit in {"swiss", "accelerated_swiss"}:
        pairing_system = explicit
    else:
        first = placement_names[0] if placement_names else "NBW"
        pairing_system = "mcmahon" if first == "MMS" else "swiss_cat" if first == "CAT" else "swiss"
    tournament_type = pairing_system
    mm_bar = general.get("genMMBar")
    mm_floor = general.get("genMMFloor")
    mm_zero = general.get("genMMZero")
    rounds = max(1, int(general.get("numberOfRounds") or 1))
    return GothaTournamentPayload(
        name=general.get("name", Path(xml_path).stem),
        short_name=general.get("shortName", Path(xml_path).stem),
        location=general.get("location", ""),
        begin_date=general.get("beginDate", ""),
        end_date=general.get("endDate", ""),
        rounds=rounds,
        players=players,
        pairing_parameters=dict(pairing_parameters.attrib) if pairing_parameters is not None else {},
        tournament_type=tournament_type,
        pairing_system=pairing_system,
        acceleration_scheme=DEFAULT_ACCELERATION_SCHEME,
        acceleration_rounds=default_acceleration_rounds(rounds),
        category_rounds=DEFAULT_CATEGORY_ROUNDS,
        bye_points=(int(general.get("genMMS2ValueBye") or 2) if tournament_type == "mcmahon" else int(general.get("genNBW2ValueBye") or 2)) / 2,
        absent_points=(int(general.get("genMMS2ValueAbsent") or 1) if tournament_type == "mcmahon" else int(general.get("genNBW2ValueAbsent") or 0)) / 2,
        mm_bar=_rank_value(mm_bar) if tournament_type == "mcmahon" and mm_bar not in (None, "") else 8,
        mm_floor=_rank_value(mm_floor) if tournament_type == "mcmahon" and mm_floor not in (None, "") else -30,
        mm_zero=_mms_offset(mm_zero, 30) if tournament_type == "mcmahon" and mm_zero not in (None, "") else 30,
        placement_criteria=",".join(criterion.get("name", "NULL") for criterion in placement_criteria),
        description=(general.get("description") or general.get("detail") or general.findtext("Description") or general.findtext("Detail") or "").strip(),
        bye_players=[(int(bye.get("roundNumber") or 0), normalize_key(bye.get("player") or "")) for bye in root.findall("ByePlayers/ByePlayer") if (bye.get("roundNumber") or "").isdigit() and int(bye.get("roundNumber") or 0) > 0 and normalize_key(bye.get("player") or "")],
    )


def create_tournament_from_gotha(conn, xml_path, pairing_system=None, player_decisions=None, metadata_overrides=None):
    """Create a tournament and its imported rounds from OpenGotha XML."""
    from config import GLICKO_K, GLICKO_M
    from services.timezone_service import current_timestamp
    from services.helpers import normalize_key
    from services.pairing_service import acceleration_for_rank, mcmahon_score_from_rank
    from services.player_service import ensure_player
    from services.tournament_pairing import (
        SUPPORTED_SYSTEMS,
        normalize_tournament_rounds,
        table_columns,
    )
    from services.tournament_participants import player_lookup, suggest_player_name
    from services.tournament_status import _refresh_tournament_completion_state

    xml_path = _resolve_gotha_path(xml_path)
    metadata = read_gotha_tournament(xml_path)
    metadata.update(metadata_overrides or {})
    pairing_system = (pairing_system or metadata["pairing_system"]).strip().lower().replace("-", "_")
    if pairing_system == "swiss_by_category":
        pairing_system = "swiss_cat"
    if pairing_system not in SUPPORTED_SYSTEMS:
        raise ValueError(f"Unknown pairing system: {pairing_system}")
    metadata.update({"pairing_system": pairing_system, "tournament_type": pairing_system})
    duplicate = conn.execute(
        """
        SELECT id FROM tournaments
        WHERE lower(name) = lower(?) AND COALESCE(begin_date, '') = COALESCE(?, '')
        ORDER BY id DESC LIMIT 1
        """,
        (metadata["name"], metadata.get("begin_date")),
    ).fetchone()
    if duplicate is not None:
        raise ValueError("Tournament with the same name and start date already exists")

    columns = table_columns(conn, "tournaments")
    tournament_rounds = normalize_tournament_rounds(metadata["rounds"])
    insert_columns = ["name", "short_name", "location", "begin_date", "end_date", "rounds", "tournament_type", "pairing_system", "bye_points", "absent_points", "placement_criteria"]
    insert_values = [metadata["name"], metadata["short_name"], metadata["location"], metadata["begin_date"], metadata["end_date"], tournament_rounds, pairing_system, pairing_system, metadata["bye_points"], metadata["absent_points"], metadata["placement_criteria"]]
    optional_values = (("description", metadata.get("description", "")), ("mm_bar", metadata.get("mm_bar", 8)), ("mm_floor", metadata.get("mm_floor", -30)), ("mm_zero", metadata.get("mm_zero", 30)), ("acceleration_scheme", metadata.get("acceleration_scheme") or DEFAULT_ACCELERATION_SCHEME), ("category_rounds", int(metadata.get("category_rounds") or DEFAULT_CATEGORY_ROUNDS)))
    for column, value in optional_values:
        if column in columns:
            insert_columns.append(column); insert_values.append(value)
    if "acceleration_rounds" in columns:
        insert_columns.append("acceleration_rounds"); insert_values.append(metadata.get("acceleration_rounds") or default_acceleration_rounds(tournament_rounds))
    if "handicap_enabled" in columns:
        has_handicap = any(int(game.get("handicap") or 0) > 0 for game in ET.parse(xml_path).getroot().findall("Games/Game") if str(game.get("handicap") or "0").strip().lstrip("-").isdigit())
        insert_columns.append("handicap_enabled"); insert_values.append(int(has_handicap))
    insert_columns.extend(["status", "source_format", "created_at"]); insert_values.extend(["draft", "OpenGotha XML", current_timestamp()])
    placeholders = ", ".join("?" for _ in insert_values)
    conn.execute(f"INSERT INTO tournaments ({', '.join(insert_columns)}) VALUES ({placeholders})", insert_values)
    tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    lookup = player_lookup(conn); pending_columns = table_columns(conn, "tournament_pending_players"); matched = 0

    for participant in metadata["players"]:
        source_key = participant["key"]; decision = (player_decisions or {}).get(source_key, "auto")
        if decision == "reject":
            continue
        player = lookup.get(source_key)
        if decision not in ("auto", "new"):
            try: selected_id = int(decision)
            except (TypeError, ValueError) as exc: raise ValueError("Invalid player reconciliation decision") from exc
            player = conn.execute("SELECT id, rating FROM players WHERE id = ? AND active = 1", (selected_id,)).fetchone()
            if player is None: raise ValueError("Selected player does not exist")
        if player is None:
            if decision == "new":
                player_id = ensure_player(conn, participant["display_name"], rating=participant["rating"], initial_rating=participant["rating"], active=1)
                player = conn.execute("SELECT id, rating FROM players WHERE id = ?", (player_id,)).fetchone()
            else:
                pending_columns_insert = ["tournament_id", "display_name", "suggested_name", "rating", "rank", "category", "source_key"]
                pending_values = [tournament_id, participant["display_name"], suggest_player_name(participant["display_name"], conn), participant["rating"], participant["rank"], participant["category"], source_key]
                if "participating" in pending_columns: pending_columns_insert.append("participating"); pending_values.append(participant["participating"])
                pending_columns_insert.append("created_at"); pending_values.append(current_timestamp())
                conn.execute(f"INSERT OR IGNORE INTO tournament_pending_players ({', '.join(pending_columns_insert)}) VALUES ({', '.join('?' for _ in pending_values)})", pending_values)
                continue
            lookup[source_key] = player
        rank = participant["rank"]
        initial_score = mcmahon_score_from_rank(rank, bar=metadata.get("mm_bar", 8), floor=metadata.get("mm_floor", -30), zero=metadata.get("mm_zero", 30)) if pairing_system == "mcmahon" else 0
        acceleration = acceleration_for_rank(rank, len(metadata["players"]), scheme=metadata.get("acceleration_scheme"), player_rank=rank) if pairing_system == "accelerated_swiss" else 0
        seed_rating = player["rating"] if player["rating"] is not None else participant["rating"] or 0
        participant_columns = table_columns(conn, "tournament_participants")
        if pairing_system == "mcmahon" and "mc_seeds_calculated" in participant_columns:
            conn.execute("INSERT OR IGNORE INTO tournament_participants (tournament_id, player_id, seed_rating, seed_rank, category, initial_score, acceleration, mc_seeds_calculated) VALUES (?, ?, ?, ?, ?, ?, ?, 1)", (tournament_id, player["id"], seed_rating, rank, participant["category"], initial_score, acceleration))
        else:
            conn.execute("INSERT OR IGNORE INTO tournament_participants (tournament_id, player_id, seed_rating, seed_rank, category, initial_score, acceleration) VALUES (?, ?, ?, ?, ?, ?, ?)", (tournament_id, player["id"], seed_rating, rank, participant["category"], initial_score, acceleration))
        matched += 1

    root = ET.parse(xml_path).getroot(); participant_names = {player["key"]: player["display_name"] for player in metadata["players"]}; round_ids = {}
    def ensure_round(round_number):
        if round_number not in round_ids:
            conn.execute("INSERT OR IGNORE INTO tournament_rounds (tournament_id, round_number, status) VALUES (?, ?, 'completed')", (tournament_id, round_number))
            round_ids[round_number] = conn.execute("SELECT id FROM tournament_rounds WHERE tournament_id = ? AND round_number = ?", (tournament_id, round_number)).fetchone()[0]
        return round_ids[round_number]
    for game in root.findall("Games/Game"):
        round_number = int(game.get("roundNumber") or 0)
        if round_number <= 0: continue
        round_id = ensure_round(round_number); white_key = normalize_key(game.get("whitePlayer") or ""); black_key = normalize_key(game.get("blackPlayer") or "")
        white_player = lookup.get(white_key); black_player = lookup.get(black_key); white_name = (white_player["display_name"] if white_player else None) or participant_names.get(white_key); black_name = (black_player["display_name"] if black_player else None) or participant_names.get(black_key)
        if white_name is None and black_name is None: continue
        result = {"RESULT_WHITEWINS": "1-0", "RESULT_BLACKWINS": "0-1", "RESULT_EQUAL": "1/2-1/2"}.get(game.get("result"))
        if result is None: continue
        board_number = game.get("tableNumber") or conn.execute("SELECT COALESCE(MAX(board_number), 0) + 1 FROM tournament_pairings WHERE round_id = ?", (round_id,)).fetchone()[0]
        try: handicap_stones = int(game.get("handicap") or 0)
        except (TypeError, ValueError): handicap_stones = 0
        is_bye = white_player is not None and black_player is None and not black_name
        conn.execute("INSERT INTO tournament_pairings (round_id, board_number, white_player_id, black_player_id, white_player_name, black_player_name, result, is_bye, handicap_stones) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (round_id, int(board_number), white_player["id"] if white_player else None, black_player["id"] if black_player else None, white_name if white_player is None else None, black_name if black_player is None else None, result, int(is_bye), handicap_stones))
        if is_bye and white_player is not None:
            conn.execute("INSERT OR REPLACE INTO tournament_round_players (round_id, player_id, status) VALUES (?, ?, 'bye')", (round_id, white_player["id"]))
            conn.execute("UPDATE tournament_participants SET received_bye = 1 WHERE tournament_id = ? AND player_id = ?", (tournament_id, white_player["id"]))
    for round_number, player_key in metadata.bye_players:
        if round_number > metadata.rounds: continue
        round_id = ensure_round(round_number); player = lookup.get(player_key); player_name = participant_names.get(player_key)
        if player is None and not player_name: continue
        if conn.execute("SELECT 1 FROM tournament_pairings WHERE round_id = ? AND is_bye = 1", (round_id,)).fetchone() is None:
            board_number = conn.execute("SELECT COALESCE(MAX(board_number), 0) + 1 FROM tournament_pairings WHERE round_id = ?", (round_id,)).fetchone()[0]
            conn.execute("INSERT INTO tournament_pairings (round_id, board_number, white_player_id, white_player_name, result, is_bye) VALUES (?, ?, ?, ?, NULL, 1)", (round_id, board_number, player["id"] if player else None, None if player else player_name))
        if player is not None:
            conn.execute("INSERT OR REPLACE INTO tournament_round_players (round_id, player_id, status) VALUES (?, ?, 'bye')", (round_id, player["id"]))
            conn.execute("UPDATE tournament_participants SET received_bye = 1 WHERE tournament_id = ? AND player_id = ?", (tournament_id, player["id"]))
    for participant in metadata.players:
        player = lookup.get(participant["key"])
        for round_number, flag in enumerate(participant["participating"][:metadata.rounds], 1):
            if flag != "0":
                continue
            round_id = ensure_round(round_number)
            if player is None:
                continue
            implied = conn.execute("SELECT 1 FROM tournament_pairings WHERE round_id = ? AND (white_player_id = ? OR black_player_id = ?)", (round_id, player["id"], player["id"])).fetchone()
            if implied is None:
                conn.execute("INSERT OR REPLACE INTO tournament_round_players (round_id, player_id, status) VALUES (?, ?, 'absent')", (round_id, player["id"]))
    _refresh_tournament_completion_state(conn, tournament_id)
    conn.execute("UPDATE tournaments SET status = 'draft' WHERE id = ?", (tournament_id,)); conn.commit()
    return tournament_id, metadata.to_dict(), matched


def export_tournament_results(conn, tournament_id):
    """Serialize a tournament as an OpenGotha-compatible XML document."""
    from datetime import datetime
    from services.category_service import glicko_to_category
    from services.tournament_pairing import normalize_tournament_rounds, table_columns

    tournament = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    if tournament is None:
        raise ValueError("Tournament not found")
    columns = set(tournament.keys())
    participant_rows = conn.execute(
        """
        SELECT tp.player_id, tp.seed_rank, tp.seed_rating, tp.category,
               player.first_name, player.last_name, player.display_name, player.rating
        FROM tournament_participants tp JOIN players player ON player.id = tp.player_id
        WHERE tp.tournament_id = ? ORDER BY tp.seed_rank, tp.id
        """, (tournament_id,)
    ).fetchall()
    pending_participation = "participating" if "participating" in table_columns(conn, "tournament_pending_players") else "'11111111111111111111' AS participating"
    pending_rows = conn.execute(f"SELECT id, display_name, rating, rank, category, {pending_participation} FROM tournament_pending_players WHERE tournament_id = ? ORDER BY rank, id", (tournament_id,)).fetchall()
    pairing_rows = conn.execute(
        """
        SELECT r.round_number, p.board_number, p.white_player_id, p.black_player_id,
               COALESCE(white.display_name, p.white_player_name) AS white_name,
               COALESCE(black.display_name, p.black_player_name) AS black_name,
               p.result, p.is_bye, p.handicap_stones
        FROM tournament_pairings p JOIN tournament_rounds r ON r.id = p.round_id
        LEFT JOIN players white ON white.id = p.white_player_id
        LEFT JOIN players black ON black.id = p.black_player_id
        WHERE r.tournament_id = ? ORDER BY r.round_number, p.board_number
        """, (tournament_id,)
    ).fetchall()
    absent_rows = conn.execute("SELECT trp.player_id, r.round_number FROM tournament_round_players trp JOIN tournament_rounds r ON r.id = trp.round_id WHERE r.tournament_id = ? AND trp.status = 'absent'", (tournament_id,)).fetchall()
    absent_rounds = {}
    for row in absent_rows:
        absent_rounds.setdefault(row["player_id"], set()).add(row["round_number"])

    root = ET.Element("Tournament", {"externalIPAddress": "127.0.0.1", "fullVersionNumber": "3.52.03", "privateTournament": "false", "remoteUUID": "00000000-0000-0000-0000-000000000000", "runningMode": "SAL", "saveDT": datetime.now().strftime("%Y%m%d%H%M%S")})
    players_element = ET.SubElement(root, "Players")
    player_keys = {}; name_keys = {}; used_keys = set()
    def player_key(last_name, first_name):
        return f"{normalize_text(last_name).replace(' ', '').upper()}{normalize_text(first_name).replace(' ', '').upper()}" if last_name and first_name else ""
    def add_player(player_id, display_name, rating, category, participating=None, first_name=None, last_name=None):
        display_name = str(display_name or "").strip(); first_name = str(first_name or "").strip(); last_name = str(last_name or "").strip()
        if not first_name and not last_name and display_name:
            parts = display_name.split(" ", 1); first_name = parts[0]; last_name = parts[1] if len(parts) > 1 else ""
        key = player_key(last_name, first_name)
        if not key: return None
        base_key = key; suffix = 2
        while key in used_keys: key = f"{base_key}{suffix}"; suffix += 1
        used_keys.add(key)
        if player_id is not None: player_keys[player_id] = key
        if display_name: name_keys[normalize_key(display_name)] = key
        try: numeric_rating = float(rating or 1500)
        except (TypeError, ValueError): numeric_rating = 1500.0
        rank_label = str(category or "").strip() or glicko_to_category(numeric_rating)
        mask = list((participating or "1" * 20)[:20].ljust(20, "1"))
        for round_number in absent_rounds.get(player_id, ()):
            if 1 <= round_number <= 20: mask[round_number - 1] = "0"
        ET.SubElement(players_element, "Player", {"name": last_name, "firstName": first_name, "userName": "", "country": "", "club": "", "egfPin": "", "ffgLicence": "", "ffgLicenceStatus": "", "agaId": "", "agaExpirationDate": "", "rank": rank_label, "rating": str(round(numeric_rating)), "ratingOrigin": "INI", "grade": rank_label, "smmsCorrection": "0", "participating": "".join(mask), "registeringStatus": "FIN"})
        return key
    for row in participant_rows:
        add_player(row["player_id"], row["display_name"], row["seed_rating"] or row["rating"], row["category"], first_name=row["first_name"], last_name=row["last_name"])
    for row in pending_rows:
        add_player(None, row["display_name"], row["rating"], row["category"], participating=row["participating"])
    for row in pairing_rows:
        for player_id, name in ((row["white_player_id"], row["white_name"]), (row["black_player_id"], row["black_name"])):
            if player_id is not None and player_id not in player_keys:
                player = conn.execute("SELECT id, first_name, last_name, display_name, rating FROM players WHERE id = ?", (player_id,)).fetchone()
                if player: add_player(player["id"], player["display_name"], player["rating"], "", first_name=player["first_name"], last_name=player["last_name"])
            elif player_id is None and normalize_key(name) not in name_keys:
                add_player(None, name, DEFAULT_RATING, "")
    games = ET.SubElement(root, "Games"); bye_rows = []; result_codes = {"1-0": "RESULT_WHITEWINS", "0-1": "RESULT_BLACKWINS", "1/2-1/2": "RESULT_EQUAL"}
    for row in pairing_rows:
        white_key = player_keys.get(row["white_player_id"]) or name_keys.get(normalize_key(row["white_name"]))
        black_key = player_keys.get(row["black_player_id"]) or name_keys.get(normalize_key(row["black_name"]))
        if row["is_bye"]:
            if white_key: bye_rows.append((row["round_number"], white_key))
            continue
        if white_key and black_key:
            ET.SubElement(games, "Game", {"blackPlayer": black_key, "handicap": str(row["handicap_stones"] or 0), "knownColor": "true", "result": result_codes.get(row["result"], "RESULT_UNKNOWN"), "roundNumber": str(row["round_number"]), "tableNumber": str(row["board_number"]), "whitePlayer": white_key})
    byes = ET.SubElement(root, "ByePlayers")
    for round_number, key in sorted(set(bye_rows)): ET.SubElement(byes, "ByePlayer", {"roundNumber": str(round_number), "player": key})
    rounds = normalize_tournament_rounds(tournament["rounds"]); bye_points = float(tournament["bye_points"] or 0); absent_points = float(tournament["absent_points"] or 0)
    mm_floor = tournament["mm_floor"] if "mm_floor" in columns else -30; mm_bar = tournament["mm_bar"] if "mm_bar" in columns else 8; mm_zero = tournament["mm_zero"] if "mm_zero" in columns else 30
    try: mm_zero_rank = -int(mm_zero)
    except (TypeError, ValueError): mm_zero_rank = -30
    parameters = ET.SubElement(root, "TournamentParameterSet")
    ET.SubElement(parameters, "GeneralParameterSet", {"shortName": str(tournament["short_name"] or tournament["name"] or "tournament"), "name": str(tournament["name"] or ""), "location": str(tournament["location"] or ""), "director": "", "beginDate": str(tournament["begin_date"] or ""), "endDate": str(tournament["end_date"] or tournament["begin_date"] or ""), "bInternetGame": "false", "basicTime": "0", "complementaryTimeSystem": "SUDDENDEATH", "stdByoYomiTime": "30", "nbMovesCanTime": "15", "canByoYomiTime": "300", "fischerTime": "0", "size": "19", "komi": "7.5", "numberOfRounds": str(rounds), "numberOfCategories": "1", "numberOfBZHGroups": "1", "genMMFloor": f"{abs(int(mm_floor))}K" if int(mm_floor) < 0 else f"{int(mm_floor)+1}D", "genMMBar": f"{int(mm_bar)+1}D" if int(mm_bar) >= 0 else f"{abs(int(mm_bar))}K", "genMMZero": f"{abs(mm_zero_rank)}K", "genNBW2ValueAbsent": str(round(absent_points * 2)), "genNBW2ValueBye": str(round(bye_points * 2)), "genMMS2ValueAbsent": str(round(absent_points * 2)), "genMMS2ValueBye": str(round(bye_points * 2)), "genRoundDownNBWMMS": "true", "genCountNotPlayedGamesAsHalfPoint": "false"})
    handicap_enabled = bool(tournament["handicap_enabled"] or 0) if "handicap_enabled" in columns else True
    ET.SubElement(parameters, "HandicapParameterSet", {"hdBase": "RANK", "hdNoHdRankThreshold": "1D" if handicap_enabled else "30K", "hdCorrection": "0", "hdCeiling": "9" if handicap_enabled else "0"})
    placement = ET.SubElement(parameters, "PlacementParameterSet"); criteria = ET.SubElement(placement, "PlacementCriteria")
    for number, criterion in enumerate([item.strip() for item in str(tournament["placement_criteria"] or "NBW").split(",") if item.strip()] or ["NBW"], 1): ET.SubElement(criteria, "PlacementCriterion", {"number": str(number), "name": criterion})
    ET.SubElement(parameters, "PairingParameterSet", {"paiMaSeedSystem1": "SPLITANDSLIP" if str(tournament["pairing_system"] or "swiss") == "accelerated_swiss" else "SPLITANDFOLD", "paiMaSeedSystem2": "SPLITANDFOLD", "paiMaAdditionalPlacementCritSystem1": "Rating", "paiMaAdditionalPlacementCritSystem2": "Rating", "paiSeMinimizeHandicap": "0"})
    ET.SubElement(parameters, "DPParameterSet", {"playerSortType": "name", "gameFormat": "short", "showPlayerGrade": "true", "showPlayerCountry": "false", "showPlayerClub": "true", "showByePlayer": "true", "showNotPairedPlayers": "true", "showNotParticipatingPlayers": "false", "showNotFinallyRegisteredPlayers": "true", "displayNPPlayers": "false", "displayNumCol": "true", "displayPlCol": "true", "displayCoCol": "true", "displayClCol": "true", "displayIndGamesInMatches": "true"})
    ET.SubElement(parameters, "PublishParameterSet", {"print": "true", "exportToLocalFile": "true", "htmlAutoScroll": "false"})
    team = ET.SubElement(root, "TeamTournamentParameterSet"); ET.SubElement(team, "TeamGeneralParameterSet", {"teamSize": "4"}); ET.SubElement(ET.SubElement(team, "TeamPlacementParameterSet"), "PlacementCriteria")
    ET.indent(root, space="  ")
    return "\ufeff<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"no\"?>\n" + ET.tostring(root, encoding="unicode", short_empty_elements=True) + "\n"
