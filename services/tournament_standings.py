"""Tournament-specific standings assembly."""
from config import DEFAULT_RATING
from services.category_service import category_value
from services.pairing_service import acceleration_for_rank, default_acceleration_rounds
from services.standings_service import calculate_standings


def get_tournament_standings(conn, tournament_id):
    """Load tournament state and calculate its OpenGotha-style standings."""
    from services.tournament_participants import list_tournament_participants

    tournament = conn.execute(
        "SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    if tournament is None:
        raise ValueError("Tournament not found")
    participant_rows = list_tournament_participants(conn, tournament_id)
    pending_id_by_name = {
        row["display_name"]: row["player_id"]
        for row in participant_rows
        if row["is_pending"]
    }
    players = [
        {
            "id": row["player_id"] if row["player_id"] is not None else row["id"],
            "name": row["display_name"],
            "rating": row["seed_rating"],
            "initial_score": row["initial_score"],
            "acceleration": row["acceleration"],
            "category": row["category"],
        }
        for row in participant_rows
    ]
    game_rows = conn.execute(
        """
        SELECT p.white_player_id, p.black_player_id,
               p.white_player_name, p.black_player_name,
               p.result, p.is_bye, r.round_number
        FROM tournament_pairings p
        JOIN tournament_rounds r ON r.id = p.round_id
        WHERE r.tournament_id = ?
        """,
        (tournament_id,),
    ).fetchall()
    games = []
    for row in game_rows:
        white_id = row["white_player_id"] or pending_id_by_name.get(
            row["white_player_name"])
        black_id = row["black_player_id"] or pending_id_by_name.get(
            row["black_player_name"])
        games.append({
            "white_player_id": white_id,
            "black_player_id": black_id,
            "result": row["result"],
            "is_bye": row["is_bye"],
            "round_number": row["round_number"],
        })
    absent_rows = conn.execute(
        """
        SELECT rp.player_id AS white_player_id, NULL AS black_player_id,
               NULL AS result, 0 AS is_bye, 1 AS is_absent,
               r.round_number
        FROM tournament_round_players rp
        JOIN tournament_rounds r ON r.id = rp.round_id
        WHERE r.tournament_id = ? AND rp.status = 'absent'
        """,
        (tournament_id,),
    ).fetchall()
    games.extend(
        {
            "white_player_id": row["white_player_id"],
            "black_player_id": row["black_player_id"],
            "result": row["result"],
            "is_bye": row["is_bye"],
            "is_absent": row["is_absent"],
            "round_number": row["round_number"],
        }
        for row in absent_rows
    )
    scored_rounds = [
        game["round_number"]
        for game in games
        if game.get("result") or game.get("is_bye") or game.get("is_absent")
    ]
    current_round = max(scored_rounds, default=0)
    acceleration_rounds = (
        int(tournament["acceleration_rounds"])
        if "acceleration_rounds" in tournament.keys() and tournament["acceleration_rounds"] is not None
        else default_acceleration_rounds(tournament["rounds"] if "rounds" in tournament.keys() else 1)
    )
    acceleration_active = (
        tournament["pairing_system"] == "accelerated_swiss"
        and 1 <= current_round <= acceleration_rounds
    )
    if tournament["pairing_system"] == "accelerated_swiss":
        seed_order = sorted(
            players, key=lambda player: (-float(player["rating"] or 0), player["id"]))
        for seed_rank, player in enumerate(seed_order, 1):
            player["acceleration"] = (
                acceleration_for_rank(
                    seed_rank,
                    len(players),
                    scheme=tournament["acceleration_scheme"] if "acceleration_scheme" in tournament.keys(
                    ) else None,
                    player_rank=round(category_value(
                        player["rating"] or DEFAULT_RATING)),
                )
                if acceleration_active
                else 0.0
            )
    settings = conn.execute(
        "SELECT bye_points, absent_points FROM tournaments WHERE id = ?", (
            tournament_id,)
    ).fetchone()
    return calculate_standings(
        players,
        games,
        tournament["pairing_system"],
        bye_points=settings["bye_points"] if settings else 1.0,
        absent_points=settings["absent_points"] if settings else 0.0,
    )
