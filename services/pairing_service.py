"""Deterministic tournament pairing algorithms compatible with OpenGotha concepts."""

from collections import defaultdict
import re

import networkx as nx


PAIRING_SYSTEMS = {"swiss", "swiss_cat", "accelerated_swiss", "mcmahon"}
SEED_SYSTEMS = {"split_random", "split_fold", "split_slip"}
DEFAULT_ACCELERATION_SCHEME = "34:2,33:1,33:0"
ACCELERATION_SCHEMES = {
    "go_three_band": {
        "scheme": DEFAULT_ACCELERATION_SCHEME,
        "label": "Go three-band (+2/+1/+0)",
    },
    "top_half": {
        "scheme": "50:1,50:0",
        "label": "Top half (+1/+0)",
    },
    "top_quarter": {
        "scheme": "25:2,25:1,50:0",
        "label": "Top quarter pressure (+2/+1/+0)",
    },
}
DEFAULT_ACCELERATION_CATEGORIES = 3
DEFAULT_ACCELERATION_FLOORS = (0, -5)
MIN_CATEGORY_RANK = -30
MAX_CATEGORY_RANK = 8
MAX_ACCELERATION_CATEGORIES = 10
DEFAULT_ACCELERATION_ROUNDS = 2
DEFAULT_CATEGORY_ROUNDS = 0


def format_rank_category(rank):
    """Format an integer rank unit as a Go category label."""
    rank = int(rank)
    return f"{abs(rank)} kyu" if rank < 0 else f"{rank + 1} dan"


def parse_rank_category(category):
    """Parse a Go category label into an integer rank unit."""
    if not isinstance(category, str):
        raise ValueError("Acceleration category floors must use dan or kyu labels")
    match = re.fullmatch(r"(\d+)\s*(dan|kyu)", category.strip(), re.IGNORECASE)
    if not match or int(match.group(1)) < 1:
        raise ValueError("Acceleration category floors must use dan or kyu labels")
    value, label = int(match.group(1)), match.group(2).lower()
    return value - 1 if label == "dan" else -value


def _player_id(player):
    return player["id"] if isinstance(player, dict) else player


def _value(player, key, default=None):
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


def _can_pair(first, second, category_strict=False):
    if category_strict and _value(first, "category", "") != _value(second, "category", ""):
        return False
    return _player_id(second) not in _played(first) and _player_id(first) not in _played(second)


def _color_assignment(first, second):
    first_balance = _color_balance(first)
    second_balance = _color_balance(second)

    if first_balance > second_balance:
        return _player_id(second), _player_id(first)
    if second_balance > first_balance:
        return _player_id(first), _player_id(second)
    return _player_id(first), _player_id(second)


def _pair_group(group, category_strict=False):
    """Pair a score group, floating the last player when necessary."""
    remaining = list(group)
    pairings = []
    floats = []

    while remaining:
        first = remaining.pop(0)
        partner_index = next(
            (index for index, candidate in enumerate(remaining) if _can_pair(first, candidate, category_strict)),
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


def _groups(players, system, category_strict=False):
    grouped = defaultdict(list)
    for player in sorted(players, key=lambda item: _sort_key(item, system)):
        group_key = (_value(player, "category", ""), _effective_score(player, system)) if category_strict else _effective_score(player, system)
        grouped[group_key].append(player)
    return [grouped[key] for key in sorted(grouped, reverse=True)]


def _concavity(value, factor=0.5):
    """Return OpenGotha's concave preference for a normalized gap."""
    normalized = max(0.0, min(1.0, float(value)))
    return (1.0 - normalized) * (1.0 + factor * normalized)


def _default_seed_system(system):
    if system == "swiss_cat":
        return "split_random"
    return "split_slip"


def _pairing_positions(players, system):
    """Return each player's position within its OpenGotha score group."""
    groups = defaultdict(list)
    for player in sorted(players, key=lambda item: _sort_key(item, system)):
        category = _value(player, "category", "") if system == "swiss_cat" else ""
        groups[(category, _effective_score(player, system))].append(player)

    positions = {}
    for group in groups.values():
        for placement, player in enumerate(group):
            positions[_player_id(player)] = (len(group), placement)
    return positions


def _seed_weight(first, second, positions, system, seed_system=None):
    first_group_size, first_placement = positions[_player_id(first)]
    second_group_size, second_placement = positions[_player_id(second)]
    if (
        first_group_size != second_group_size
        or first_group_size < 2
        or _effective_score(first, system) != _effective_score(second, system)
        or (system == "swiss_cat" and _value(first, "category", "") != _value(second, "category", ""))
    ):
        return 0

    group_size = first_group_size
    seed_system = seed_system or _default_seed_system(system)
    if seed_system not in SEED_SYSTEMS:
        raise ValueError(f"Unknown seeding system: {seed_system}")
    max_weight = 5_000_000
    if seed_system == "split_random":
        different_halves = (2 * first_placement < group_size) != (2 * second_placement < group_size)
        if not different_halves:
            return 0
        name = "|".join(sorted((str(_value(first, "name", _player_id(first))), str(_value(second, "name", _player_id(second))))))
        deterministic_value = sum(ord(character) * (index + 1) for index, character in enumerate(name))
        return int(max_weight * 0.8) + deterministic_value % int(max_weight * 0.2)
    if seed_system == "split_fold":
        x = first_placement + second_placement - (group_size - 1)
        return max_weight - int(max_weight * (x * x) / max(1, (group_size - 1) ** 2))

    x = 2 * abs(first_placement - second_placement) - group_size
    return max_weight - int(max_weight * (x * x) / (group_size**2))


def _draw_up_down_weight(first, second, positions, system):
    if _effective_score(first, system) == _effective_score(second, system):
        return 0
    first_group_size, first_placement = positions[_player_id(first)]
    second_group_size, second_placement = positions[_player_id(second)]
    first_is_upper = _effective_score(first, system) > _effective_score(second, system)
    upper_size, upper_placement = (first_group_size, first_placement) if first_is_upper else (second_group_size, second_placement)
    lower_size, lower_placement = (second_group_size, second_placement) if first_is_upper else (first_group_size, first_placement)
    previous_up = sum(int(_value(player, "draw_up_count", 0) or 0) for player in (first, second))
    previous_down = sum(int(_value(player, "draw_down_count", 0) or 0) for player in (first, second))
    position_weight = (upper_size - 1 - upper_placement) + lower_placement
    correction_weight = 2_000_000 if previous_up != previous_down else 0
    return correction_weight - position_weight * 100_000


def _pair_weight(first, second, players, system, positions, seed_system=None):
    first_score = _effective_score(first, system)
    second_score = _effective_score(second, system)
    score_range = max(
        1.0,
        max(_effective_score(player, system) for player in players)
        - min(_effective_score(player, system) for player in players),
    )
    score_weight = int(100_000_000_000 * _concavity(abs(first_score - second_score) / score_range))

    category_gap = 0
    if system == "swiss_cat":
        category_gap = int(_value(first, "category_order", 0) or 0) - int(_value(second, "category_order", 0) or 0)
        if not category_gap:
            category_gap = 0 if _value(first, "category", "") == _value(second, "category", "") else 1
    category_count = max(1, len({str(_value(player, "category", "")) for player in players}))
    category_weight = int(20_000_000_000 * _concavity(abs(category_gap) / category_count))

    balance_gap = abs(_color_balance(first) - _color_balance(second))
    color_weight = max(0, 1_000_000 - balance_gap * 250_000)
    if _color_balance(first) * _color_balance(second) < 0:
        color_weight += 1_000_000
    if (_color_balance(first) == 0 and abs(_color_balance(second)) >= 2) or (
        _color_balance(second) == 0 and abs(_color_balance(first)) >= 2
    ):
        color_weight += 500_000

    geographic_weight = 0
    if _value(first, "country", "") and _value(first, "country", "") == _value(second, "country", ""):
        geographic_weight -= 1_000
    if _value(first, "club", "") and _value(first, "club", "") == _value(second, "club", ""):
        geographic_weight -= 2_000

    return (
        score_weight
        + category_weight
        + _draw_up_down_weight(first, second, positions, system)
        + _seed_weight(first, second, positions, system, seed_system)
        + color_weight
        + geographic_weight
    )


def _pair_without_repeats(players, system, seed_system=None, category_strict=False):
    """Find a maximum-weight legal matching, as OpenGotha does."""
    ordered_players = sorted(players, key=lambda item: _sort_key(item, system))
    positions = _pairing_positions(ordered_players, system)
    graph = nx.Graph()
    graph.add_nodes_from(_player_id(player) for player in ordered_players)
    for index, first in enumerate(ordered_players):
        for second in ordered_players[index + 1 :]:
            if _can_pair(first, second, category_strict):
                graph.add_edge(
                    _player_id(first),
                    _player_id(second),
                    weight=_pair_weight(first, second, ordered_players, system, positions, seed_system),
                )

    matching = nx.max_weight_matching(graph, maxcardinality=True, weight="weight")
    if len(matching) * 2 < len(ordered_players):
        return None

    player_by_id = {_player_id(player): player for player in ordered_players}
    pairs = []
    for first_id, second_id in matching:
        first = player_by_id[first_id]
        second = player_by_id[second_id]
        white_id, black_id = _color_assignment(first, second)
        pairs.append({"white_player_id": white_id, "black_player_id": black_id, "is_bye": False})
    return sorted(pairs, key=lambda pairing: (str(pairing["white_player_id"]), str(pairing["black_player_id"])))


def pair_players(players, system="swiss", seed_system=None, category_strict=None):
    """Generate one deterministic round of pairings.

    Each player should contain ``id``, ``rating``, ``score``, ``opponents`` and
    ``colors``. Optional ``initial_score`` supports McMahon and ``acceleration``
    supports Accelerated Swiss. The returned list contains one bye at most.
    """
    if system not in PAIRING_SYSTEMS:
        raise ValueError(f"Unknown pairing system: {system}")
    if category_strict is None:
        category_strict = system == "swiss_cat"

    working_players = list(players)
    if category_strict:
        pairings = []
        grouped_players = defaultdict(list)
        for player in working_players:
            grouped_players[_value(player, "category", "")].append(player)
        for category in sorted(grouped_players, key=str.casefold):
            category_players = grouped_players[category]
            category_bye = None
            if len(category_players) % 2:
                category_bye_player = _choose_bye(category_players, system)
                category_players = [
                    player for player in category_players if player is not category_bye_player
                ]
                category_bye = _assign_bye([category_bye_player], system)

            category_pairings = _pair_without_repeats(
                category_players, system, seed_system, category_strict=True
            )
            if category_pairings is None:
                category_groups = _groups(category_players, system, category_strict=True)
                category_pairings = []
                floating = []
                for group in category_groups:
                    group_pairings, group_floats = _pair_group(
                        floating + group, category_strict=True
                    )
                    category_pairings.extend(group_pairings)
                    floating = group_floats
                while len(floating) > 1:
                    first = floating.pop(0)
                    second = floating.pop(0)
                    white_id, black_id = _color_assignment(first, second)
                    category_pairings.append(
                        {
                            "white_player_id": white_id,
                            "black_player_id": black_id,
                            "is_bye": False,
                        }
                    )
                if floating:
                    raise ValueError("Strict category pairing left an unpaired player")
            pairings.extend(category_pairings)
            if category_bye:
                pairings.append(category_bye)
        return pairings

    bye = None
    if len(working_players) % 2:
        bye_player = _choose_bye(working_players, system)
        working_players.remove(bye_player)
        bye = _assign_bye([bye_player], system)

    pairings = _pair_without_repeats(working_players, system, seed_system, category_strict)
    floating = []
    if pairings is None:
        groups = _groups(working_players, system, category_strict)
        pairings = []
        for group in groups:
            group_pairings, group_floats = _pair_group(floating + group, category_strict)
            pairings.extend(group_pairings)
            floating = group_floats

    if floating:
        # Score-group constraints can leave floaters when previous-opponent
        # restrictions are tight. Pair compatible floaters before resorting
        # to a repeat, so no player silently disappears from the round.
        while len(floating) > 1:
            first = floating.pop(0)
            partner_index = next(
                (index for index, candidate in enumerate(floating) if _can_pair(first, candidate, category_strict)),
                None,
            )
            if partner_index is None:
                floating.insert(0, first)
                break
            second = floating.pop(partner_index)
            white_id, black_id = _color_assignment(first, second)
            pairings.append(
                {
                    "white_player_id": white_id,
                    "black_player_id": black_id,
                    "is_bye": False,
                }
            )

    if floating and category_strict:
        raise ValueError("Strict category pairing cannot pair all players within their categories")

    if bye:
        pairings.append(bye)

    return pairings


def _acceleration_bands(scheme):
    if scheme is None:
        scheme = DEFAULT_ACCELERATION_SCHEME
    if not isinstance(scheme, str):
        raise ValueError("Acceleration scheme must be text")
    if scheme.startswith("categories:"):
        category_count, floors = parse_acceleration_categories(scheme)
        if category_count == 1:
            return ((1.0, 0.0),)
        fraction = 1.0 / category_count
        return tuple(
            (fraction, max(0.0, 1.0 - index / (category_count - 1)))
            for index in range(category_count)
        )
    bands = []
    for item in scheme.split(","):
        parts = item.strip().split(":")
        if len(parts) != 2:
            raise ValueError("Acceleration scheme must use percentage:bonus entries")
        fraction, bonus = float(parts[0]) / 100.0, float(parts[1])
        if fraction <= 0 or bonus < 0:
            raise ValueError("Acceleration scheme values must be non-negative")
        bands.append((fraction, bonus))
    if not bands or abs(sum(fraction for fraction, _ in bands) - 1.0) > 1e-6:
        raise ValueError("Acceleration scheme percentages must total 100")
    return tuple(bands)


def validate_acceleration_categories(number_of_categories, floors):
    """Validate OpenGotha-style category count and rank-unit floors."""
    try:
        category_count = int(number_of_categories)
    except (TypeError, ValueError) as exc:
        raise ValueError("Number of acceleration categories must be an integer") from exc
    if not 1 <= category_count <= MAX_ACCELERATION_CATEGORIES:
        raise ValueError(
            f"Number of acceleration categories must be between 1 and {MAX_ACCELERATION_CATEGORIES}"
        )

    if floors is None:
        floor_values = []
    else:
        floor_values = list(floors)
    if len(floor_values) != category_count - 1:
        raise ValueError("Acceleration categories require one floor between each category")

    normalized_floors = []
    for floor in floor_values:
        if isinstance(floor, str) and not re.fullmatch(r"[+-]?\d+(?:\.0+)?", floor.strip()):
            normalized_floors.append(parse_rank_category(floor))
            continue
        try:
            numeric_floor = float(floor)
        except (TypeError, ValueError) as exc:
            raise ValueError("Acceleration category floors must use dan or kyu labels") from exc
        if not numeric_floor.is_integer():
            raise ValueError("Acceleration category floors must be integers")
        normalized_floors.append(int(numeric_floor))
    if any(
        floor < MIN_CATEGORY_RANK or floor > MAX_CATEGORY_RANK
        for floor in normalized_floors
    ):
        raise ValueError(f"Acceleration category floors must be between {MIN_CATEGORY_RANK} and {MAX_CATEGORY_RANK}")
    if any(first <= second for first, second in zip(normalized_floors, normalized_floors[1:])):
        raise ValueError("Acceleration category floors must descend from strongest to weakest")
    return category_count, tuple(normalized_floors)


def serialize_acceleration_categories(number_of_categories, floors):
    """Return a compact storage representation for category floor settings."""
    category_count, normalized_floors = validate_acceleration_categories(
        number_of_categories, floors
    )
    floor_text = ",".join(str(floor) for floor in normalized_floors)
    return f"categories:{category_count};floors:{floor_text}"


def parse_acceleration_categories(scheme):
    """Parse the stored OpenGotha-style category floor representation."""
    if not isinstance(scheme, str) or not scheme.startswith("categories:"):
        raise ValueError("Acceleration scheme is not category-based")
    fields = {}
    for field in scheme.split(";"):
        name, separator, value = field.partition(":")
        if not separator or name in fields:
            raise ValueError("Invalid acceleration category settings")
        fields[name] = value
    if set(fields) != {"categories", "floors"}:
        raise ValueError("Invalid acceleration category settings")
    floors = [] if not fields["floors"].strip() else fields["floors"].split(",")
    return validate_acceleration_categories(fields["categories"], floors)


def acceleration_category_settings(scheme=None):
    """Return category editor values for stored or legacy acceleration data."""
    if isinstance(scheme, str) and scheme.startswith("categories:"):
        return parse_acceleration_categories(scheme)
    if scheme == DEFAULT_ACCELERATION_SCHEME or scheme is None:
        return DEFAULT_ACCELERATION_CATEGORIES, DEFAULT_ACCELERATION_FLOORS
    band_count = len(_acceleration_bands(scheme))
    if band_count == 1:
        return 1, ()
    step = (MAX_CATEGORY_RANK - MIN_CATEGORY_RANK) / band_count
    floors = tuple(
        round(MAX_CATEGORY_RANK - step * index)
        for index in range(1, band_count)
    )
    return validate_acceleration_categories(band_count, floors)


def validate_acceleration_scheme(scheme):
    """Validate and return a stored Accelerated Swiss band scheme."""
    _acceleration_bands(scheme)
    return scheme.strip()


def acceleration_for_rank(rank, player_count, scheme=None, player_rank=None):
    """Return OpenGotha-style acceleration points for an initial seed rank.

    The top half starts one point ahead and the next quarter starts half a
    point ahead. This keeps early rounds from pairing the strongest seed with
    the weakest seed while remaining deterministic for imported events.
    """
    if rank <= 0 or player_count <= 1:
        return 0.0
    if scheme is None:
        scheme = DEFAULT_ACCELERATION_SCHEME
    if isinstance(scheme, str) and scheme.startswith("categories:") and player_rank is not None:
        category_count, floors = parse_acceleration_categories(scheme)
        category = sum(int(player_rank) < floor for floor in floors)
        if category_count <= 1:
            return 0.0
        return max(0.0, 1.0 - category / (category_count - 1))

    covered = 0
    for fraction, bonus in _acceleration_bands(scheme):
        covered += max(1, round(player_count * fraction))
        if rank <= covered:
            return bonus
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
