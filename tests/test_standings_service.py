import sqlite3

from services.standings_service import calculate_standings


def players(initial_scores=None):
    initial_scores = initial_scores or {}
    return [
        {"id": player_id, "name": f"Player {player_id}", "rating": 2000 - player_id, "initial_score": initial_scores.get(player_id, 0)}
        for player_id in range(1, 5)
    ]


def test_swiss_standings_use_score_then_sos_and_sosos():
    games = [
        {"white_player_id": 1, "black_player_id": 2, "result": "1-0"},
        {"white_player_id": 3, "black_player_id": 4, "result": "1/2-1/2"},
        {"white_player_id": 1, "black_player_id": 3, "result": "1/2-1/2"},
    ]

    result = calculate_standings(players(), games)

    assert [row["id"] for row in result] == [1, 3, 4, 2]
    assert result[0]["score"] == 1.5
    assert result[0]["sos"] == 1.0
    assert result[0]["rank"] == 1


def test_swiss_standings_use_sodos_before_rating_for_equal_score_sos_and_sosos():
    players_with_scores = [
        {"id": 1, "name": "Player 1", "rating": 1500, "initial_score": 0},
        {"id": 2, "name": "Player 2", "rating": 1700, "initial_score": 0},
        {"id": 3, "name": "Player 3", "rating": 1500, "initial_score": 0},
        {"id": 4, "name": "Player 4", "rating": 1400, "initial_score": 0},
    ]
    games = [
        {"white_player_id": 1, "black_player_id": 2, "result": "1/2-1/2"},
        {"white_player_id": 1, "black_player_id": 3, "result": "0-1"},
        {"white_player_id": 2, "black_player_id": 1, "result": "1/2-1/2"},
    ]

    result = calculate_standings(players_with_scores, games)

    assert [row["id"] for row in result] == [1, 3, 2, 4]
    assert result[1]["sodos"] > result[2]["sodos"]


def test_standings_assign_unique_positions_when_all_tiebreakers_are_equal():
    tied_players = [
        {"id": 1, "name": "Zulu", "rating": 1500, "initial_score": 0},
        {"id": 2, "name": "Alpha", "rating": 1500, "initial_score": 0},
        {"id": 3, "name": "Bravo", "rating": 1500, "initial_score": 0},
    ]

    result = calculate_standings(tied_players, [])

    assert [row["name"] for row in result] == ["Alpha", "Bravo", "Zulu"]
    assert [row["rank"] for row in result] == [1, 2, 3]


def test_mcmahon_uses_initial_score_as_primary_score():
    games = [{"white_player_id": 2, "black_player_id": 3, "result": "1-0"}]

    result = calculate_standings(players({1: 2, 2: 1}), games, "mcmahon")

    assert result[0]["id"] == 2
    assert result[0]["primary_score"] == 2
    assert result[1]["id"] == 1
    assert result[1]["primary_score"] == 2


def test_mcmahon_standings_expose_seed_mms_value():
    games = [{"white_player_id": 2, "black_player_id": 3, "result": "1-0"}]

    result = calculate_standings(players({1: 2, 2: 1}), games, "mcmahon")

    assert result[0]["mms"] == 1
    assert result[1]["mms"] == 2


def test_accelerated_swiss_uses_acceleration_in_primary_score_and_sorting():
    players_with_acceleration = [
        {"id": 1, "name": "Player 1", "rating": 1800, "initial_score": 0, "acceleration": 1.0},
        {"id": 2, "name": "Player 2", "rating": 2000, "initial_score": 0, "acceleration": 0.0},
    ]

    result = calculate_standings(players_with_acceleration, [], "accelerated_swiss")

    assert [row["id"] for row in result] == [1, 2]
    assert result[0]["primary_score"] == 1.0


def test_bye_counts_as_one_point_and_is_not_an_opponent():
    games = [{"white_player_id": 1, "black_player_id": None, "is_bye": True}]

    result = calculate_standings(players(), games)

    first = result[0]
    assert first["id"] == 1
    assert first["score"] == 1.0
    assert first["bye_count"] == 1
    assert first["opponents"] == set()


def test_bye_value_can_be_zero_or_half_point():
    games = [{"white_player_id": 1, "black_player_id": None, "is_bye": True}]

    half_point = calculate_standings(players(), games, bye_points=0.5)
    zero_points = calculate_standings(players(), games, bye_points=0)

    assert half_point[0]["score"] == 0.5
    assert zero_points[0]["score"] == 0


def test_sqlite_rows_are_supported():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE players (id INTEGER, name TEXT, rating REAL, initial_score REAL)"
    )
    conn.execute("INSERT INTO players VALUES (1, 'Player 1', 1500, 0)")
    conn.commit()
    player_rows = conn.execute("SELECT * FROM players").fetchall()

    result = calculate_standings(player_rows, [])

    assert result[0]["id"] == 1
    assert result[0]["name"] == "Player 1"


def test_round_results_use_opponent_final_rank_and_result_marker():
    games = [
        {"round_number": 1, "white_player_id": 1, "black_player_id": 2, "result": "1-0"},
        {"round_number": 2, "white_player_id": 1, "black_player_id": 3, "result": "0-1"},
    ]

    result = calculate_standings(players(), games)
    player_one = next(row for row in result if row["id"] == 1)

    assert player_one["round_results"] == [
        {"round": 1, "opponent": next(row["rank"] for row in result if row["id"] == 2), "result": "+"},
        {"round": 2, "opponent": next(row["rank"] for row in result if row["id"] == 3), "result": "-"},
    ]
