"""OpenGotha-style tournament scoring and placement criteria."""

from services.pairing_service import effective_score_for_player


def _format_rank_label(category):
    text = str(category or "").strip()
    if not text:
        return ""
    normalized = text.upper()
    if normalized.endswith("D"):
        return f"{normalized[:-1]}d"
    if normalized.endswith("K"):
        return f"{normalized[:-1]}k"
    return text.lower()


def _value(row, key, default=None):
    """Read both dictionaries and sqlite3.Row objects."""
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _result_points(result, player_id, white_player_id, black_player_id):
    if result in (None, ""):
        return 0.0, False
    if result == "1-0":
        return (1.0 if player_id == white_player_id else 0.0), True
    if result == "0-1":
        return (1.0 if player_id == black_player_id else 0.0), True
    if result == "1/2-1/2":
        return 0.5, True
    return 0.0, False


def calculate_standings(
    players,
    games,
    tournament_type="swiss",
    bye_points=1.0,
    absent_points=0.0,
):
    """Calculate score, SOS, SOSOS, and defeated-opponent tiebreaks.

    This mirrors OpenGotha's core placement model: Swiss uses the recorded
    score as the primary score, accelerated Swiss adds acceleration points, and
    McMahon adds the initial MMS-like seed score. The returned rows are sorted
    by primary score, SOS, SOSOS, SODOS, rating, and finally name. Positions
    are always sequential so tied tiebreak values do not produce duplicate
    displayed ranks.
    """
    standings = {
        _value(player, "id"): {
            "id": _value(player, "id"),
            "name": _value(player, "name", str(_value(player, "id"))),
            "category": _value(player, "category", ""),
            "rank_label": _format_rank_label(_value(player, "category", "")),
            "rating": float(_value(player, "rating", 0) or 0),
            "initial_score": float(_value(player, "initial_score", 0) or 0),
            "acceleration": float(_value(player, "acceleration", 0) or 0),
            "score": 0.0,
            "wins": 0,
            "opponents": set(),
            "defeated_opponents": set(),
            "bye_count": 0,
        }
        for player in players
    }

    for game in games:
        white_id = _value(game, "white_player_id")
        black_id = _value(game, "black_player_id")
        if white_id not in standings:
            continue
        if _value(game, "is_bye", 0):
            standings[white_id]["score"] += float(bye_points)
            standings[white_id]["bye_count"] += 1
            continue
        if _value(game, "is_absent", 0):
            standings[white_id]["score"] += float(absent_points)
            continue
        if black_id not in standings:
            continue
        result = _value(game, "result")
        white_points, played = _result_points(result, white_id, white_id, black_id)
        black_points, _ = _result_points(result, black_id, white_id, black_id)
        if not played:
            continue
        standings[white_id]["score"] += white_points
        standings[black_id]["score"] += black_points
        standings[white_id]["opponents"].add(black_id)
        standings[black_id]["opponents"].add(white_id)
        if white_points == 1.0:
            standings[white_id]["wins"] += 1
            standings[white_id]["defeated_opponents"].add(black_id)
        elif black_points == 1.0:
            standings[black_id]["wins"] += 1
            standings[black_id]["defeated_opponents"].add(white_id)

    for row in standings.values():
        row["mms"] = float(row["initial_score"])
        row["primary_score"] = effective_score_for_player(row, tournament_type)

    for row in standings.values():
        row["sos"] = sum(standings[opponent]["primary_score"] for opponent in row["opponents"])
        row["sodos"] = sum(standings[opponent]["primary_score"] for opponent in row["defeated_opponents"])

    for row in standings.values():
        row["sosos"] = sum(standings[opponent]["sos"] for opponent in row["opponents"])

    ordered = sorted(
        standings.values(),
        key=lambda row: (
            -row["primary_score"],
            -row["sos"],
            -row["sosos"],
            -row["sodos"],
            -row["rating"],
            row["name"].casefold(),
            row["id"],
        ),
    )
    for index, row in enumerate(ordered, 1):
        row["rank"] = index

    rank_by_player = {row["id"]: row["rank"] for row in ordered}
    for row in ordered:
        row["round_results"] = []
    for game in games:
        white_id = _value(game, "white_player_id")
        black_id = _value(game, "black_player_id")
        if white_id not in rank_by_player:
            continue
        round_number = _value(game, "round_number")
        if _value(game, "is_bye", 0):
            ordered_player = standings[white_id]
            ordered_player["round_results"].append({"round": round_number, "opponent": "BYE", "result": "+"})
            continue
        if _value(game, "is_absent", 0):
            standings[white_id]["round_results"].append({"round": round_number, "opponent": "ABS", "result": "-"})
            continue
        if black_id not in rank_by_player:
            continue
        result = _value(game, "result")
        white_result = "+" if result == "1-0" else "-" if result == "0-1" else "=" if result == "1/2-1/2" else "?"
        black_result = "+" if result == "0-1" else "-" if result == "1-0" else "=" if result == "1/2-1/2" else "?"
        standings[white_id]["round_results"].append({"round": round_number, "opponent": rank_by_player[black_id], "result": white_result})
        standings[black_id]["round_results"].append({"round": round_number, "opponent": rank_by_player[white_id], "result": black_result})
    return ordered
