"""Deterministic tournament pairing algorithms compatible with OpenGotha concepts."""

from collections import defaultdict
from functools import lru_cache


PAIRING_SYSTEMS = {"swiss", "swiss_cat", "accelerated_swiss", "mcmahon"}


def _player_id(player):
    return player["id"] if isinstance(player, dict) else player


def _value(player, key, default=0):
    if isinstance(player, dict):
        return player.get(key, default)
    return default


def effective_score_for_player(player, system):
    """Return the shared primary score used by pairing and standings.

    Accelerated Swiss adds the player's acceleration bonus to their current
    score; McMahon adds the seed-based initial score. Standard Swiss uses the
    recorded score alone.
    """
    score = float(_value(player, "score", 0))
    if system == "accelerated_swiss":
        return score + float(_value(player, "acceleration", 0))
    if system == "mcmahon":
        return score + float(_value(player, "initial_score", 0))
    return score


def _played(player):
    return set(_value(player, "opponents", ()))


def _color_balance(player):
    colors = _value(player, "colors", {}) or {}
    return colors.get("white", 0) - colors.get("black", 0)


def _effective_score(player, system):
    return effective_score_for_player(player, system)


def _sort_key(player, system):
    return (
        -_effective_score(player, system),
        str(_value(player, "category", "")).casefold() if system == "swiss_cat" else "",
        -float(_value(player, "rating", 0)),
        str(_value(player, "name", _player_id(player))).casefold(),
        _player_id(player),
    )


def _can_pair(first, second):
    return _player_id(second) not in _played(first) and _player_id(first) not in _played(second)


def _color_assignment(first, second):
    first_balance = _color_balance(first)
    second_balance = _color_balance(second)

    if first_balance > second_balance:
        return _player_id(second), _player_id(first)
    if second_balance > first_balance:
        return _player_id(first), _player_id(second)
    return _player_id(first), _player_id(second)


def _pair_group(group):
    """Pair a score group, floating the last player when necessary."""
    remaining = list(group)
    pairings = []
    floats = []

    while remaining:
        first = remaining.pop(0)
        partner_index = next(
            (index for index, candidate in enumerate(remaining) if _can_pair(first, candidate)),
            None,
        )
        if partner_index is None:
            floats.append(first)
            continue

        second = remaining.pop(partner_index)
        white_id, black_id = _color_assignment(first, second)
        pairings.append(
            {
                "white_player_id": white_id,
                "black_player_id": black_id,
                "is_bye": False,
            }
        )

    return pairings, floats


def _assign_bye(players, system):
    if not players:
        return None
    candidate = sorted(
        players,
        key=lambda player: (
            bool(_value(player, "received_bye", False)),
            _effective_score(player, system),
            float(_value(player, "rating", 0)),
            str(_value(player, "name", _player_id(player))).casefold(),
            _player_id(player),
        ),
    )[0]
    return {
        "white_player_id": _player_id(candidate),
        "black_player_id": None,
        "is_bye": True,
    }


def _choose_bye(players, system):
    """Choose a bye before score groups are paired.

    OpenGotha tracks BYE participation independently from score groups. A
    player who has not received a BYE is always preferred, then the lowest
    effective score and lowest rating are used as deterministic tie-breaks.
    """
    return sorted(
        players,
        key=lambda player: (
            bool(_value(player, "received_bye", False)),
            _effective_score(player, system),
            float(_value(player, "rating", 0)),
            str(_value(player, "name", _player_id(player))).casefold(),
            _player_id(player),
        ),
    )[0]


def _groups(players, system):
    grouped = defaultdict(list)
    for player in sorted(players, key=lambda item: _sort_key(item, system)):
        grouped[_effective_score(player, system)].append(player)
    return [grouped[key] for key in sorted(grouped, reverse=True)]


def _pair_without_repeats(players, system):
    player_by_id = {_player_id(player): player for player in players}
    ordered_ids = tuple(
        _player_id(player)
        for player in sorted(players, key=lambda item: _sort_key(item, system))
    )

    @lru_cache(maxsize=None)
    def search(remaining_ids):
        if not remaining_ids:
            return ()
        first_id = remaining_ids[0]
        first = player_by_id[first_id]
        candidate_ids = sorted(
            remaining_ids[1:],
            key=lambda candidate_id: (
                abs(_effective_score(first, system) - _effective_score(player_by_id[candidate_id], system)),
                0 if system != "swiss_cat" or _value(first, "category", "") == _value(player_by_id[candidate_id], "category", "") else 1,
                abs(_color_balance(first) - _color_balance(player_by_id[candidate_id])),
                -float(_value(player_by_id[candidate_id], "rating", 0)),
                candidate_id,
            ),
        )
        for candidate_id in candidate_ids:
            candidate = player_by_id[candidate_id]
            if not _can_pair(first, candidate):
                continue
            rest = tuple(item for item in remaining_ids[1:] if item != candidate_id)
            result = search(rest)
            if result is not None:
                white_id, black_id = _color_assignment(first, candidate)
                return ((white_id, black_id),) + result
        return None

    result = search(ordered_ids)
    if result is None:
        return None
    return [
        {"white_player_id": white_id, "black_player_id": black_id, "is_bye": False}
        for white_id, black_id in result
    ]


def pair_players(players, system="swiss"):
    """Generate one deterministic round of pairings.

    Each player should contain ``id``, ``rating``, ``score``, ``opponents`` and
    ``colors``. Optional ``initial_score`` supports McMahon and ``acceleration``
    supports Accelerated Swiss. The returned list contains one bye at most.
    """
    if system not in PAIRING_SYSTEMS:
        raise ValueError(f"Unknown pairing system: {system}")

    working_players = list(players)
    bye = None
    if len(working_players) % 2:
        bye_player = _choose_bye(working_players, system)
        working_players.remove(bye_player)
        bye = _assign_bye([bye_player], system)

    pairings = _pair_without_repeats(working_players, system)
    floating = []
    if pairings is None:
        groups = _groups(working_players, system)
        pairings = []
        for group in groups:
            group_pairings, group_floats = _pair_group(floating + group)
            pairings.extend(group_pairings)
            floating = group_floats

    if floating:
        # Score-group constraints can leave floaters when previous-opponent
        # restrictions are tight. Pair compatible floaters before resorting
        # to a repeat, so no player silently disappears from the round.
        while len(floating) > 1:
            first = floating.pop(0)
            partner_index = next(
                (index for index, candidate in enumerate(floating) if _can_pair(first, candidate)),
                0,
            )
            second = floating.pop(partner_index)
            white_id, black_id = _color_assignment(first, second)
            pairings.append(
                {
                    "white_player_id": white_id,
                    "black_player_id": black_id,
                    "is_bye": False,
                }
            )

    if bye:
        pairings.append(bye)

    return pairings


def acceleration_for_rank(rank, player_count):
    """Return OpenGotha-style acceleration points for an initial seed rank.

    The top half starts one point ahead and the next quarter starts half a
    point ahead. This keeps early rounds from pairing the strongest seed with
    the weakest seed while remaining deterministic for imported events.
    """
    if rank <= 0 or player_count <= 1:
        return 0.0
    if rank <= max(1, player_count // 2):
        return 1.0
    if rank <= max(1, (player_count * 3) // 4):
        return 0.5
    return 0.0


def mcmahon_initial_score(rank, player_count, group_size=4):
    """Calculate deterministic McMahon start points from a seed rank."""
    if rank <= 0 or player_count <= 1:
        return 0.0
    return float((player_count - rank) // max(1, group_size))


def mcmahon_score_from_rank(rank, bar=3, floor=-30, zero=0):
    """Convert a McMahon seed position into the OpenGotha-style MMS start value.

    In OpenGotha, the strongest seed starts at the MM bar and each subsequent
    seed drops by one point until the floor is reached. The XML also includes a
    zero-offset (for example, ``30K``) that shifts the displayed MMS values into
    the positive range used by many Go tournaments. The offset is configurable
    and defaults to zero so existing manual tournaments keep their legacy values.
    """
    bar_value = int(bar)
    floor_value = int(floor)
    zero_value = int(zero)
    if bar_value <= 0:
        return float(floor_value + zero_value)
    rank = max(1, int(rank))
    return float(max(floor_value, bar_value - rank + 1) + zero_value)
