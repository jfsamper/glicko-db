import pytest

from services.pairing_service import (
    acceleration_for_rank,
    format_rank_category,
    parse_acceleration_categories,
    parse_rank_category,
    mcmahon_initial_score,
    mcmahon_score_from_rank,
    pair_players,
    serialize_acceleration_categories,
    validate_acceleration_categories,
    validate_acceleration_scheme,
)


def make_players(count=8):
    return [
        {
            "id": index,
            "name": f"Player {index}",
            "rating": 2000 - index * 10,
            "score": 0,
            "opponents": set(),
            "colors": {"white": 0, "black": 0},
        }
        for index in range(1, count + 1)
    ]


def played_ids(pairings):
    return {
        player_id
        for pairing in pairings
        for player_id in (pairing["white_player_id"], pairing["black_player_id"])
        if player_id is not None
    }


@pytest.mark.parametrize("system", ["swiss", "accelerated_swiss", "mcmahon"])
def test_pairing_systems_pair_every_player_once(system):
    players = make_players()
    if system == "accelerated_swiss":
        for rank, player in enumerate(players, 1):
            player["acceleration"] = acceleration_for_rank(rank, len(players))
    if system == "mcmahon":
        for rank, player in enumerate(players, 1):
            player["initial_score"] = mcmahon_initial_score(rank, len(players))

    pairings = pair_players(players, system)

    assert len(pairings) == 4
    assert all(not pairing["is_bye"] for pairing in pairings)
    assert played_ids(pairings) == set(range(1, 9))


def test_swiss_avoids_previous_opponents_and_assigns_a_bye():
    players = make_players(5)
    for player in players:
        player["opponents"] = {player["id"] + 1} if player["id"] < 5 else set()

    pairings = pair_players(players, "swiss")

    assert len(pairings) == 3
    assert sum(pairing["is_bye"] for pairing in pairings) == 1
    assert len(played_ids(pairings)) == 5
    assert all(
        pairing["is_bye"]
        or pairing["black_player_id"] not in players[pairing["white_player_id"] - 1]["opponents"]
        for pairing in pairings
    )


def test_bye_rotates_across_rounds_before_repeating():
    players = make_players(5)
    bye_players = []

    for _ in range(4):
        pairings = pair_players(players, "swiss")
        bye = next(pairing for pairing in pairings if pairing["is_bye"])
        bye_players.append(bye["white_player_id"])
        for pairing in pairings:
            if pairing["is_bye"]:
                players[pairing["white_player_id"] - 1]["received_bye"] = True
                continue
            white = players[pairing["white_player_id"] - 1]
            black = players[pairing["black_player_id"] - 1]
            white["opponents"].add(black["id"])
            black["opponents"].add(white["id"])

    assert len(set(bye_players)) == 4
    assert set(bye_players) <= set(range(1, 6))


def test_bye_selection_uses_the_tournament_score_model():
    players = [
        {"id": 1, "name": "Player 1", "rating": 1500, "score": 0, "initial_score": 0, "acceleration": 0, "opponents": set(), "colors": {"white": 0, "black": 0}},
        {"id": 2, "name": "Player 2", "rating": 1500, "score": 0, "initial_score": 5, "acceleration": 0, "opponents": set(), "colors": {"white": 0, "black": 0}},
        {"id": 3, "name": "Player 3", "rating": 1500, "score": 0, "initial_score": 10, "acceleration": 0, "opponents": set(), "colors": {"white": 0, "black": 0}},
    ]

    pairings = pair_players(players, "mcmahon")
    bye = next(pairing for pairing in pairings if pairing["is_bye"])

    assert bye["white_player_id"] == 1


def test_color_balance_prefers_black_for_player_with_more_white_games():
    players = make_players(2)
    players[0]["colors"] = {"white": 2, "black": 0}
    players[1]["colors"] = {"white": 0, "black": 1}

    pairings = pair_players(players)

    assert pairings == [
        {"white_player_id": 2, "black_player_id": 1, "is_bye": False}
    ]


def test_acceleration_and_mcmahon_seeding_points_are_deterministic():
    assert acceleration_for_rank(1, 8) == 1.0
    assert acceleration_for_rank(6, 8) == 0.5
    assert acceleration_for_rank(8, 8) == 0.0
    assert mcmahon_initial_score(1, 8) == 1.0
    assert mcmahon_initial_score(8, 8) == 0.0


def test_acceleration_scheme_can_configure_seed_bands():
    scheme = validate_acceleration_scheme("25:2,25:1,50:0")

    assert [acceleration_for_rank(rank, 8, scheme=scheme) for rank in (1, 2, 3, 4, 5, 8)] == [2.0, 2.0, 1.0, 1.0, 0.0, 0.0]


def test_acceleration_scheme_rejects_incomplete_bands():
    with pytest.raises(ValueError, match="total 100"):
        validate_acceleration_scheme("50:1,25:0.5")


def test_acceleration_categories_round_trip_and_apply_rank_floors():
    scheme = serialize_acceleration_categories(3, (0, -5))

    assert scheme == "categories:3;floors:0,-5"
    assert parse_acceleration_categories(scheme) == (3, (0, -5))
    assert acceleration_for_rank(1, 8, scheme=scheme, player_rank=1) == 1.0
    assert acceleration_for_rank(1, 8, scheme=scheme, player_rank=-2) == 0.5
    assert acceleration_for_rank(1, 8, scheme=scheme, player_rank=-6) == 0.0


@pytest.mark.parametrize(
    ("category", "rank"),
    [("1 dan", 0), ("3 dan", 2), ("16 kyu", -16), (" 3 DAN ", 2)],
)
def test_rank_category_labels_convert_to_rank_units(category, rank):
    assert parse_rank_category(category) == rank
    assert format_rank_category(rank) == category.strip().lower()


def test_swiss_by_category_keeps_pairings_inside_strict_sections():
    players = make_players(6)
    for index, player in enumerate(players):
        player["category"] = "3D" if index < 2 else "5K"

    pairings = pair_players(players, "swiss_cat")

    assert all(
        pairing["is_bye"]
        or players[pairing["white_player_id"] - 1]["category"]
        == players[pairing["black_player_id"] - 1]["category"]
        for pairing in pairings
    )
    assert sum(pairing["is_bye"] for pairing in pairings) == 0


def test_swiss_by_category_rejects_multiple_odd_sections():
    players = make_players(6)
    for index, player in enumerate(players):
        player["category"] = "3D" if index < 1 else ("5K" if index < 2 else "10K")

    with pytest.raises(ValueError, match="odd-sized category"):
        pair_players(players, "swiss_cat")


@pytest.mark.parametrize(
    "category_count, floors, message",
    [
        (0, [], "between 1 and"),
        (3, [0], "one floor"),
        (3, [0, 0], "descend"),
        (2, [9], "between -30 and 8"),
    ],
)
def test_acceleration_categories_reject_invalid_floor_configurations(category_count, floors, message):
    with pytest.raises(ValueError, match=message):
        validate_acceleration_categories(category_count, floors)


def test_mcmahon_score_from_rank_rewards_higher_seeds():
    assert mcmahon_score_from_rank(1, bar=8, floor=-30) == 8.0
    assert mcmahon_score_from_rank(8, bar=8, floor=-30) == 1.0
    assert mcmahon_score_from_rank(1, bar=8, floor=-30) > mcmahon_score_from_rank(8, bar=8, floor=-30)


def test_mcmahon_score_from_rank_applies_configurable_zero_offset():
    assert mcmahon_score_from_rank(1, bar=3, floor=-20, zero=30) == 33.0
    assert mcmahon_score_from_rank(19, bar=3, floor=-20, zero=30) == 15.0


def test_unknown_pairing_system_is_rejected():
    with pytest.raises(ValueError, match="Unknown pairing system"):
        pair_players(make_players(), "round_robin")


def test_multi_round_swiss_scenario_matrix_odd_players_and_byes():
    """Verify 5-round Swiss tournament pairing matrix with 7 players.

    Covers:
    - Round 1 BYE assigned to the lowest-rated/lowest-seeded player
    - Exactly 1 BYE per round
    - All 5 BYEs assigned to 5 distinct players (no repeat BYEs)
    - No duplicate matchups across all rounds
    - Color balance maintained across rounds
    """
    num_players = 7
    rounds = 5
    players = [
        {
            "id": i,
            "name": f"Player {i}",
            "rating": 2000 - i * 50,
            "score": 0.0,
            "opponents": set(),
            "colors": {"white": 0, "black": 0},
            "received_bye": False,
        }
        for i in range(1, num_players + 1)
    ]

    bye_recipients = []
    paired_matchups = set()

    for round_num in range(1, rounds + 1):
        pairings = pair_players(players, system="swiss")
        assert len(pairings) == 4  # 3 games + 1 bye

        byes = [p for p in pairings if p["is_bye"]]
        assert len(byes) == 1
        bye_pid = byes[0]["white_player_id"]
        bye_recipients.append(bye_pid)

        # In round 1, lowest ranked/rated player gets the bye
        if round_num == 1:
            assert bye_pid == 7

        for p in pairings:
            if p["is_bye"]:
                player = next(pl for pl in players if pl["id"] == p["white_player_id"])
                player["received_bye"] = True
                player["score"] += 1.0
            else:
                w_id = p["white_player_id"]
                b_id = p["black_player_id"]
                matchup = tuple(sorted((w_id, b_id)))
                assert matchup not in paired_matchups, f"Duplicate matchup {matchup} in round {round_num}"
                paired_matchups.add(matchup)

                w_player = next(pl for pl in players if pl["id"] == w_id)
                b_player = next(pl for pl in players if pl["id"] == b_id)
                w_player["opponents"].add(b_id)
                b_player["opponents"].add(w_id)
                w_player["colors"]["white"] += 1
                b_player["colors"]["black"] += 1

                # Deterministic winner: higher rated player wins
                if w_player["rating"] >= b_player["rating"]:
                    w_player["score"] += 1.0
                else:
                    b_player["score"] += 1.0

    # 5 distinct players received the 5 byes
    assert len(set(bye_recipients)) == 5
    # Color balance: difference between white and black games is at most 2 for all players
    for pl in players:
        diff = abs(pl["colors"]["white"] - pl["colors"]["black"])
        assert diff <= 2, f"Player {pl['id']} color balance imbalance too high: {pl['colors']}"


def test_multi_round_mcmahon_scenario_matrix():
    """Verify multi-round McMahon pairing behavior across score bands."""
    players = [
        {
            "id": i,
            "name": f"Player {i}",
            "rating": 2200 - i * 40,
            "score": 0.0,
            "initial_score": 2.0 if i <= 4 else 0.0,
            "opponents": set(),
            "colors": {"white": 0, "black": 0},
            "received_bye": False,
        }
        for i in range(1, 9)
    ]

    paired_matchups = set()

    for round_num in range(1, 4):
        pairings = pair_players(players, system="mcmahon")
        assert len(pairings) == 4
        assert all(not p["is_bye"] for p in pairings)

        # In round 1, top band (1-4, MMS=2) should pair strictly among themselves
        if round_num == 1:
            top_band_ids = {1, 2, 3, 4}
            bottom_band_ids = {5, 6, 7, 8}
            for p in pairings:
                pair_set = {p["white_player_id"], p["black_player_id"]}
                assert pair_set.issubset(top_band_ids) or pair_set.issubset(bottom_band_ids)

        for p in pairings:
            w_id = p["white_player_id"]
            b_id = p["black_player_id"]
            matchup = tuple(sorted((w_id, b_id)))
            assert matchup not in paired_matchups, f"Duplicate matchup {matchup} in round {round_num}"
            paired_matchups.add(matchup)

            w_player = next(pl for pl in players if pl["id"] == w_id)
            b_player = next(pl for pl in players if pl["id"] == b_id)
            w_player["opponents"].add(b_id)
            b_player["opponents"].add(w_id)
            w_player["colors"]["white"] += 1
            b_player["colors"]["black"] += 1

            if w_player["rating"] >= b_player["rating"]:
                w_player["score"] += 1.0
            else:
                b_player["score"] += 1.0


def test_swiss_cat_pairing_prioritizes_same_category_groups():
    """Verify swiss_cat pairing groups players in the same category first when scores tie."""
    players = [
        {"id": 1, "name": "A1", "rating": 2000, "score": 1, "category": "1D", "opponents": set(), "colors": {"white": 0, "black": 0}},
        {"id": 2, "name": "A2", "rating": 1900, "score": 1, "category": "1D", "opponents": set(), "colors": {"white": 0, "black": 0}},
        {"id": 3, "name": "B1", "rating": 1800, "score": 1, "category": "1K", "opponents": set(), "colors": {"white": 0, "black": 0}},
        {"id": 4, "name": "B2", "rating": 1700, "score": 1, "category": "1K", "opponents": set(), "colors": {"white": 0, "black": 0}},
    ]

    pairings = pair_players(players, system="swiss_cat")

    assert len(pairings) == 2
    assert {frozenset((p["white_player_id"], p["black_player_id"])) for p in pairings} == {
        frozenset((1, 2)),
        frozenset((3, 4)),
    }


def test_swiss_uses_split_and_slip_seeding_within_a_score_group():
    players = make_players(4)

    pairings = pair_players(players, "swiss")

    assert {frozenset((p["white_player_id"], p["black_player_id"])) for p in pairings} == {
        frozenset((1, 3)),
        frozenset((2, 4)),
    }


def test_pair_players_supports_split_and_fold_seeding():
    players = make_players(4)

    pairings = pair_players(players, "swiss", seed_system="split_fold")

    assert {frozenset((p["white_player_id"], p["black_player_id"])) for p in pairings} == {
        frozenset((1, 4)),
        frozenset((2, 3)),
    }


def test_weighted_matching_keeps_a_complete_legal_pairing():
    players = make_players(4)
    players[1]["opponents"] = {3}
    players[2]["opponents"] = {2, 4}
    players[3]["opponents"] = {1, 3}

    pairings = pair_players(players, "swiss")

    assert {frozenset((p["white_player_id"], p["black_player_id"])) for p in pairings} == {
        frozenset((1, 3)),
        frozenset((2, 4)),
    }


def test_swiss_cat_matching_prefers_intra_category_edges():
    players = [
        {"id": 1, "name": "A1", "rating": 2000, "score": 1, "category": "1D", "opponents": set(), "colors": {"white": 0, "black": 0}},
        {"id": 2, "name": "A2", "rating": 1900, "score": 1, "category": "1D", "opponents": set(), "colors": {"white": 0, "black": 0}},
        {"id": 3, "name": "B1", "rating": 1800, "score": 1, "category": "1K", "opponents": set(), "colors": {"white": 0, "black": 0}},
        {"id": 4, "name": "B2", "rating": 1700, "score": 1, "category": "1K", "opponents": set(), "colors": {"white": 0, "black": 0}},
    ]

    pairings = pair_players(players, "swiss_cat")

    assert {frozenset((p["white_player_id"], p["black_player_id"])) for p in pairings} == {
        frozenset((1, 2)),
        frozenset((3, 4)),
    }
