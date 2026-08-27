import random

import pytest

from scripts.dev_only.create_pairing_test_tournaments import (
    DEFAULT_DRAW_RATE,
    EXTRA_SCENARIOS,
    PLAYER_COUNTS,
    build_plan,
    choose_result,
)


def test_demo_results_are_decisive_when_draw_rate_is_zero():
    rng = random.Random(20260813)

    results = [choose_result(rng, 1800, 1600, draw_rate=0) for _ in range(100)]

    assert set(results) <= {"1-0", "0-1"}
    assert "1-0" in results
    assert "0-1" in results


def test_demo_result_generator_can_still_create_draw_examples_explicitly():
    assert choose_result(random.Random(1), 1800, 1600, draw_rate=1) == "1/2-1/2"


def test_demo_result_generator_rejects_invalid_draw_rate():
    with pytest.raises(ValueError, match="draw_rate"):
        choose_result(random.Random(1), 1800, 1600, draw_rate=-0.01)
    with pytest.raises(ValueError, match="draw_rate"):
        choose_result(random.Random(1), 1800, 1600, draw_rate=1.01)


def test_demo_plan_covers_odd_even_and_additional_pairing_systems():
    plan, rounds = build_plan(20260813, 5)
    scenarios = {(system, count) for system, count, _index, _rng in plan}

    assert rounds == 5
    assert {("swiss", count) for count in PLAYER_COUNTS} <= scenarios
    assert {("mcmahon", count) for count in PLAYER_COUNTS} <= scenarios
    assert {("accelerated_swiss", 17), ("swiss_cat", 18)} <= scenarios
    assert len(plan) == len(PLAYER_COUNTS) * 2 + len(EXTRA_SCENARIOS)
    assert any(count % 2 for _system, count in scenarios)
    assert any(count % 2 == 0 for _system, count in scenarios)
