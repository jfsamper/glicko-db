"""Regression tests for services/glicko2.py's Player._f() volatility solver.

Background
----------
Player._f() implements f(x) from Step 5 of the Glicko-2 algorithm
(http://www.glicko.net/glicko/glicko2.pdf), used by the Illinois-method
root finder in _newVol() to solve for the new volatility. The published
formula uses phi (the player's rating deviation on the Glicko-2 internal
scale) in both the numerator and denominator:

    f(x) = [e^x * (delta^2 - phi^2 - v - e^x)] / [2 * (phi^2 + v + e^x)^2]
           - (x - a) / tau^2

A prior version of this file substituted `self.__rating` (mu, the
player's rating) for `self.__rd` (phi, the rating deviation) in both
terms. The bug was dormant at this project's default configuration
(low fixed volatility) but produced measurable drift at higher
volatility or ratings far from the 1500 baseline. See CODE_REVIEW.md
and the 2026-08 review notes for details.

These tests pin the corrected behavior so a future edit can't
reintroduce the mu/phi swap silently.
"""
import math

import pytest

from services.glicko2 import Player, GLICKO_SCALE_FACTOR, BASE_RATING


@pytest.fixture(autouse=True)
def _restore_class_tau():
    """Player._tau is a mutable class attribute shared across instances.

    Several call sites (rating_service.py) intentionally mutate it per
    match, so tests must save/restore it to avoid leaking state into
    other tests in the suite.
    """
    original = Player._tau
    yield
    Player._tau = original


def test_f_uses_rating_deviation_not_rating():
    """Directly isolates the mu-vs-phi bug in _f().

    A player rated exactly at BASE_RATING (1500) has an internal
    rating (mu) of zero, while its internal rating deviation (phi)
    is nonzero. If _f() were to (re)use self.__rating instead of
    self.__rd, this test would see the mu=0 term drop out of the
    formula entirely and the assertion below would fail.
    """
    rd_input = 200.0
    player = Player(rating=BASE_RATING, rd=rd_input, vol=0.06)
    Player._tau = 0.5

    phi = rd_input / GLICKO_SCALE_FACTOR
    assert phi != 0  # sanity: the test only isolates the bug if phi != mu (0)

    x, delta, v, a = 0.1, 0.5, 0.3, -2.0
    ex = math.exp(x)
    expected_num = ex * (delta**2 - phi**2 - v - ex)
    expected_denom = 2 * ((phi**2 + v + ex) ** 2)
    expected = (expected_num / expected_denom) - ((x - a) / (Player._tau**2))

    actual = player._f(x, delta, v, a)

    assert actual == pytest.approx(expected, rel=1e-12)


def test_glickman_worked_example():
    """Cross-checks update_player() against the reference example from
    Glickman's Glicko-2 paper (Table 1 / worked example section):

        Player: rating=1500, RD=200, vol=0.06
        Opponents: (1400, RD 30, win), (1550, RD 100, loss), (1700, RD 300, loss)
        Expected result: rating ~= 1464.06, RD ~= 151.52, vol ~= 0.05999

    tau is fixed at 0.5 to match the paper's example.
    """
    Player._tau = 0.5
    player = Player(rating=1500, rd=200, vol=0.06)

    player.update_player(
        [1400, 1550, 1700],
        [30, 100, 300],
        [1, 0, 0],
    )

    assert player.rating == pytest.approx(1464.06, abs=0.01)
    assert player.rd == pytest.approx(151.52, abs=0.01)
    assert player.vol == pytest.approx(0.05999, abs=1e-4)


def _reference_glicko2_update(rating, rd, vol, tau, opponents):
    """Self-contained reference implementation of a single Glicko-2 update,
    independent of services/glicko2.py, used only to cross-validate the
    fixed _f() against an implementation that never had the mu/phi bug.

    opponents: list of (score, opp_rating, opp_rd) tuples.
    """
    q_scale = 173.7178
    mu = (rating - 1500) / q_scale
    phi = rd / q_scale

    def g(other_phi):
        return 1 / math.sqrt(1 + 3 * other_phi**2 / math.pi**2)

    def e(other_mu, other_phi):
        return 1 / (1 + math.exp(-g(other_phi) * (mu - other_mu)))

    scaled = [
        (score, (opp_rating - 1500) / q_scale, opp_rd / q_scale)
        for score, opp_rating, opp_rd in opponents
    ]

    variance_inv = 0.0
    diff_sum = 0.0
    for score, opp_mu, opp_phi in scaled:
        gi = g(opp_phi)
        ei = e(opp_mu, opp_phi)
        variance_inv += gi**2 * ei * (1 - ei)
        diff_sum += gi * (score - ei)
    v = 1 / variance_inv
    delta = v * diff_sum

    alpha = math.log(vol**2)
    epsilon = 1e-6

    def f(x):
        tmp = phi**2 + v + math.exp(x)
        return (math.exp(x) * (delta**2 - tmp)) / (2 * tmp**2) - (x - alpha) / tau**2

    A = alpha
    if delta**2 > phi**2 + v:
        B = math.log(delta**2 - phi**2 - v)
    else:
        k = 1
        while f(alpha - k * tau) < 0:
            k += 1
        B = alpha - k * tau

    fA, fB = f(A), f(B)
    while abs(B - A) > epsilon:
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB <= 0:
            A, fA = B, fB
        else:
            fA = fA / 2
        B, fB = C, fC

    new_vol = math.exp(A / 2)
    phi_star = math.sqrt(phi**2 + new_vol**2)
    new_phi = 1 / math.sqrt(1 / phi_star**2 + 1 / v)
    new_mu = mu + new_phi**2 * diff_sum

    new_rating = new_mu * q_scale + 1500
    new_rd = new_phi * q_scale
    return new_rating, new_rd, new_vol


@pytest.mark.parametrize(
    "rating,rd,vol,opponents",
    [
        # Baseline: Glickman's own worked example.
        (1500, 200, 0.06, [(1, 1400, 30), (0, 1550, 100), (0, 1700, 300)]),
        # High rating, high volatility, big upset loss: the mu/phi bug
        # diverged from the independent reference by ~0.02 rating points
        # and ~0.015 RD here before the fix.
        (2800, 60, 0.5, [(0, 1200, 60)]),
        # Symmetric case: low rating, high volatility, big upset win.
        (800, 60, 0.5, [(1, 2200, 60)]),
        # Moderate rating far from baseline, moderate volatility.
        (2800, 60, 0.2, [(0, 1200, 60)]),
    ],
)
def test_matches_independent_reference_implementation(rating, rd, vol, opponents):
    """Cross-validates Player.update_player() against a from-scratch
    reference implementation of the Glicko-2 algorithm for scenarios
    where the mu/phi bug previously produced visible drift (ratings far
    from the 1500 baseline combined with elevated volatility).
    """
    tau = 0.5
    Player._tau = tau

    player = Player(rating=rating, rd=rd, vol=vol)
    player.update_player(
        [opp_rating for _, opp_rating, _ in opponents],
        [opp_rd for _, _, opp_rd in opponents],
        [score for score, _, _ in opponents],
    )

    expected_rating, expected_rd, expected_vol = _reference_glicko2_update(
        rating, rd, vol, tau, opponents
    )

    # Tolerance is deliberately loose relative to the 1e-6 bisection
    # epsilon used by both root finders: two independently-coded Illinois
    # method implementations converge to within epsilon of the true root
    # but not to bit-identical values, so a handful of ULPs of residual
    # difference is expected and not itself a sign of a bug.
    assert player.rating == pytest.approx(expected_rating, abs=1e-4)
    assert player.rd == pytest.approx(expected_rd, abs=1e-4)
    assert player.vol == pytest.approx(expected_vol, abs=1e-6)