"""Tournament persistence and OpenGotha-compatible metadata import."""

from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

from config import DEFAULT_RATING, GLICKO_K, GLICKO_M
from services.category_service import category_value, glicko_to_category, suggested_handicap_stones
from services.common import current_date, current_timestamp
from services.helpers import normalize_key, normalize_text
from services.import_gotha import GothaPlayer, GothaTournamentPayload
from services.pairing_service import (
    DEFAULT_ACCELERATION_SCHEME,
    DEFAULT_ACCELERATION_ROUNDS,
    DEFAULT_CATEGORY_ROUNDS,
    acceleration_for_rank,
    mcmahon_initial_score,
    mcmahon_score_from_rank,
    pair_players,
)
from services.player_service import ensure_player
from services.reporting_service import ensure_tournament_match_identity
from services.standings_service import calculate_standings


SUPPORTED_SYSTEMS = {"swiss", "swiss_cat", "accelerated_swiss", "mcmahon"}
TOURNAMENT_STATUSES = ("draft", "active", "canceled", "completed")
VALID_TOURNAMENT_RESULTS = {"1-0", "0-1", "1/2-1/2"}


def normalize_tournament_system(value, default="swiss"):
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized == "swiss_by_category":
        normalized = "swiss_cat"
    return normalized if normalized in SUPPORTED_SYSTEMS else default


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


def _category_for_rating(conn, rating):
    config = conn.execute(
        """
        SELECT glicko_k, glicko_m
        FROM category_config
        WHERE id = 1
        """
    ).fetchone() if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'category_config'"
    ).fetchone() else None
    category = glicko_to_category(
        rating,
        k=config["glicko_k"] if config else GLICKO_K,
        m=config["glicko_m"] if config else GLICKO_M,
    )
    if category.endswith(" dan"):
        return f"{category[:-4]}D"
    if category.endswith(" kyu"):
        return f"{category[:-4]}K"
    return category


def _auto_handicap_stones(conn, white_player_id, black_player_id):
    """Best-effort auto-suggested handicap (in stones) for a newly created
    pairing, from the two players' current ratings. Returns 0 if either
    rating can't be found (e.g. a not-yet-materialized pending player) --
    callers should let a tournament director edit the result either way.
    """
    rows = conn.execute(
        "SELECT id, rating FROM players WHERE id IN (?, ?)",
        (white_player_id, black_player_id),
    ).fetchall()
    ratings = {row["id"]: row["rating"] for row in rows}
    if white_player_id not in ratings or black_player_id not in ratings:
        return 0

    config = conn.execute(
        "SELECT glicko_k, glicko_m FROM category_config WHERE id = 1"
    ).fetchone() if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'category_config'"
    ).fetchone() else None

    return suggested_handicap_stones(
        ratings[white_player_id],
        ratings[black_player_id],
        k=config["glicko_k"] if config else GLICKO_K,
        m=config["glicko_m"] if config else GLICKO_M,
    )


def _tournament_handicap_enabled(conn, tournament_id):
    columns = _table_columns(conn, "tournaments")
    if "handicap_enabled" not in columns:
        # Legacy schemas predate the setting and already auto-suggested handicaps.
        return True
    row = conn.execute(
        "SELECT handicap_enabled FROM tournaments WHERE id = ?",
        (tournament_id,),
    ).fetchone()
    return bool(row and row["handicap_enabled"])


def update_tournament_handicaps(conn, tournament_id, handicap_enabled, apply_auto_handicap=False):
    """Update existing pairings when a tournament's handicap mode changes."""
    pairings = conn.execute(
        """
        SELECT p.id, p.white_player_id, p.black_player_id, p.is_bye
        FROM tournament_pairings p
        JOIN tournament_rounds r ON r.id = p.round_id
        WHERE r.tournament_id = ?
        """,
        (tournament_id,),
    ).fetchall()
    for pairing in pairings:
        handicap_stones = 0
        if handicap_enabled and apply_auto_handicap and not pairing["is_bye"]:
            if pairing["white_player_id"] and pairing["black_player_id"]:
                handicap_stones = _auto_handicap_stones(
                    conn, pairing["white_player_id"], pairing["black_player_id"]
                )
        conn.execute(
            "UPDATE tournament_pairings SET handicap_stones = ? WHERE id = ?",
            (handicap_stones, pairing["id"]),
        )
        conn.execute(
            "UPDATE matches SET handicap_stones = ? WHERE tournament_pairing_id = ?",
            (handicap_stones, pairing["id"]),
        )


def normalize_tournament_rounds(rounds):
    """Tournament rounds must always be valid and non-zero."""
    try:
        value = int(rounds)
    except (TypeError, ValueError):
        return 1
    return max(1, value)


def _table_columns(conn, table_name):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _pairing_policy(tournament, round_number):
    acceleration_rounds = (
        int(tournament["acceleration_rounds"])
        if "acceleration_rounds" in tournament.keys() and tournament["acceleration_rounds"] is not None
        else DEFAULT_ACCELERATION_ROUNDS
    )
    category_rounds = (
        int(tournament["category_rounds"])
        if "category_rounds" in tournament.keys() and tournament["category_rounds"] is not None
        else DEFAULT_CATEGORY_ROUNDS
    )
    return {
        "acceleration_active": (
            tournament["pairing_system"] == "accelerated_swiss"
            and round_number <= max(0, acceleration_rounds)
        ),
        "category_strict": (
            tournament["pairing_system"] == "swiss_cat"
            and (category_rounds == 0 or round_number <= max(0, category_rounds))
        ),
    }


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


def _rank_label(value, default="30K"):
    try:
        numeric_value = int(value)
    except (TypeError, ValueError):
        return default
    return f"{numeric_value + 1}D" if numeric_value >= 0 else f"{abs(numeric_value)}K"


def _mms_offset(value, default=0):
    text = str(value or "").strip().upper()
    if not text:
        return default
    if text.endswith("D"):
        try:
            return int(text[:-1])
        except ValueError:
            return default
    if text.endswith("K"):
        try:
            return int(text[:-1])
        except ValueError:
            return default
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


def read_gotha_tournament(
    xml_path: str | Path, pairing_system: str | None = None
) -> GothaTournamentPayload:
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

    players: list[GothaPlayer] = []
    players_container = tournament_root.find("Players")
    if players_container is None:
        players_container = root.find("Players")
    for player in (players_container.findall("Player") if players_container is not None else []):
        first_name = player.get("firstName", "").strip()
        last_name = player.get("name", "").strip()
        try:
            rating = float(player.get("rating") or DEFAULT_RATING)
        except (TypeError, ValueError):
            rating = DEFAULT_RATING
        if rating < GLICKO_M:
            rating = float(GLICKO_M)
        players.append(
            GothaPlayer(
                key=normalize_key(f"{last_name}{first_name}"),
                display_name=f"{first_name} {last_name}".strip(),
                rating=rating,
                rank=0,
                rank_value=_rank_value(player.get("rank") or player.get("grade")),
                category=player.get("grade", ""),
                country=player.get("country", ""),
                club=player.get("club", ""),
            )
        )

    players.sort(
        key=lambda player: (
            -player.rank_value,
            -player.rating,
            player.display_name.casefold(),
        )
    )
    for rank, player in enumerate(players, 1):
        player.rank = rank

    pairing_parameters = tournament_root.find("PairingParameterSet")
    if pairing_parameters is None:
        pairing_parameters = root.find("PairingParameterSet")
    nbw2_bye = int(general.get("genNBW2ValueBye") or 2)
    mms2_bye = int(general.get("genMMS2ValueBye") or 2)
    nbw2_absent = int(general.get("genNBW2ValueAbsent") or 0)
    mms2_absent = int(general.get("genMMS2ValueAbsent") or 1)
    placement_criteria = tournament_root.findall(
        "PlacementParameterSet/PlacementCriteria/PlacementCriterion"
    )
    if not placement_criteria:
        placement_criteria = root.findall(
            "PlacementParameterSet/PlacementCriteria/PlacementCriterion"
        )
    placement_names = [
        (criterion.get("name") or "").strip().upper().replace(" ", "")
        for criterion in placement_criteria
    ]
    explicit_pairing_system = (pairing_system or "").strip().lower()
    if explicit_pairing_system in {"mcmahon", "mc-mahon"}:
        pairing_system = "mcmahon"
    elif explicit_pairing_system == "swiss_cat":
        pairing_system = "swiss_cat"
    elif explicit_pairing_system in {"swiss", "accelerated_swiss"}:
        pairing_system = explicit_pairing_system
    else:
        first_criterion = placement_names[0] if placement_names else "NBW"
        if first_criterion == "MMS":
            pairing_system = "mcmahon"
        elif first_criterion == "CAT":
            pairing_system = "swiss_cat"
        else:
            pairing_system = "swiss"
    tournament_type = pairing_system
    mm_bar = general.get("genMMBar")
    mm_floor = general.get("genMMFloor")
    mm_zero = general.get("genMMZero")
    mm_bar_value = _rank_value(mm_bar) if tournament_type == "mcmahon" and mm_bar not in (None, "") else 8
    mm_floor_value = (
        _rank_value(mm_floor)
        if tournament_type == "mcmahon" and mm_floor not in (None, "")
        else -30
    )
    mm_zero_value = (
        _mms_offset(mm_zero, default=30)
        if tournament_type == "mcmahon" and mm_zero not in (None, "")
        else 30
    )
    return GothaTournamentPayload(
        name=general.get("name", Path(xml_path).stem),
        short_name=general.get("shortName", Path(xml_path).stem),
        location=general.get("location", ""),
        begin_date=general.get("beginDate", ""),
        end_date=general.get("endDate", ""),
        rounds=normalize_tournament_rounds(general.get("numberOfRounds") or 1),
        players=players,
        pairing_parameters=dict(pairing_parameters.attrib) if pairing_parameters is not None else {},
        tournament_type=tournament_type,
        pairing_system=pairing_system,
        acceleration_scheme=DEFAULT_ACCELERATION_SCHEME,
        acceleration_rounds=DEFAULT_ACCELERATION_ROUNDS,
        category_rounds=DEFAULT_CATEGORY_ROUNDS,
        bye_points=(mms2_bye if tournament_type == "mcmahon" else nbw2_bye) / 2,
        absent_points=(mms2_absent if tournament_type == "mcmahon" else nbw2_absent) / 2,
        mm_bar=mm_bar_value,
        mm_floor=mm_floor_value,
        mm_zero=mm_zero_value,
        placement_criteria=",".join(
            criterion.get("name", "NULL") for criterion in placement_criteria
        ),
        description=(
            general.get("description")
            or general.get("detail")
            or general.findtext("Description")
            or general.findtext("Detail")
            or ""
        ).strip(),
    )


def _player_lookup(conn):
    rows = conn.execute("SELECT id, display_name, first_name, last_name, rating FROM players").fetchall()
    lookup = {}
    for row in rows:
        for value in (
            row["display_name"],
            f"{row['first_name']} {row['last_name']}",
            f"{row['last_name']}{row['first_name']}",
        ):
            if value:
                lookup[normalize_key(value)] = row
    return lookup


def _name_tokens(value):
    text = normalize_text(value).lower()
    if not text:
        return []
    tokens = []
    for raw in text.replace("-", " ").split():
        token = normalize_key(raw)
        if token:
            tokens.append(token)
    return tokens


def _name_similarity_score(target_name, candidate_name):
    target_tokens = _name_tokens(target_name)
    candidate_tokens = _name_tokens(candidate_name)
    if not target_tokens or not candidate_tokens:
        return SequenceMatcher(None, normalize_key(target_name), normalize_key(candidate_name)).ratio()

    target_set = set(target_tokens)
    candidate_set = set(candidate_tokens)
    if target_set.issubset(candidate_set) or candidate_set.issubset(target_set):
        return 0.95

    common_tokens = len(target_set & candidate_set)
    if common_tokens:
        overlap = common_tokens / max(len(target_set), len(candidate_set))
        ordered_ratio = SequenceMatcher(
            None,
            " ".join(sorted(target_tokens)),
            " ".join(sorted(candidate_tokens)),
        ).ratio()
        return max(overlap, ordered_ratio)

    ordered_ratio = SequenceMatcher(
        None,
        " ".join(sorted(target_tokens)),
        " ".join(sorted(candidate_tokens)),
    ).ratio()
    return ordered_ratio


def _suggest_player_name(name, conn):
    """Return a close DB name if the exact lookup failed, or None if no match is found."""
    if not name:
        return None
    rows = conn.execute(
        "SELECT display_name, first_name, last_name FROM players WHERE active = 1"
    ).fetchall()
    best_match = None
    best_score = 0.0
    for row in rows:
        for candidate in (
            row["display_name"],
            f"{row['first_name']} {row['last_name']}",
        ):
            if not candidate:
                continue
            score = _name_similarity_score(name, candidate)
            if score > best_score:
                best_score = score
                best_match = candidate
    if best_score >= 0.82 and best_match:
        return best_match
    return None


def _list_pending_players(conn, tournament_id):
    """Return pending (not-yet-created) players for a tournament."""
    return conn.execute(
        """
        SELECT *
        FROM tournament_pending_players
        WHERE tournament_id = ?
        ORDER BY rank, display_name
        """,
        (tournament_id,),
    ).fetchall()


def list_tournament_participants(conn, tournament_id):
    """Return matched participants plus pending (not-yet-created) players.

    Pending rows keep a public-facing ``id`` of None so callers can treat
    them as "not yet materialized" without silently dropping them, while the
    internal ``player_id`` remains a synthetic negative id to keep standings
    and pairing logic stable.
    """
    tournament_columns = _table_columns(conn, "tournaments")
    select_sql = "SELECT pairing_system"
    if {"mm_bar", "mm_floor", "mm_zero"}.issubset(tournament_columns):
        select_sql += ", mm_bar, mm_floor, mm_zero"
    select_sql += " FROM tournaments WHERE id = ?"
    tournament = conn.execute(select_sql, (tournament_id,)).fetchone()

    matched = conn.execute(
        """
        SELECT tp.player_id AS player_id, p.display_name AS display_name,
               tp.seed_rating AS seed_rating, tp.seed_rank AS seed_rank,
               tp.category AS category, tp.initial_score AS initial_score,
               tp.acceleration AS acceleration
        FROM tournament_participants tp
        JOIN players p ON p.id = tp.player_id
        WHERE tp.tournament_id = ?
        """,
        (tournament_id,),
    ).fetchall()
    pending = _list_pending_players(conn, tournament_id)

    rows = [
        {
            "id": row["player_id"],
            "player_id": row["player_id"],
            "display_name": row["display_name"],
            "seed_rating": row["seed_rating"],
            "seed_rank": row["seed_rank"],
            "category": row["category"],
            "initial_score": row["initial_score"],
            "acceleration": row["acceleration"],
            "is_pending": False,
        }
        for row in matched
    ]

    pending_initial_scores = {}
    if tournament and tournament["pairing_system"] == "mcmahon":
        mm_bar = tournament["mm_bar"] if "mm_bar" in tournament.keys() and tournament["mm_bar"] is not None else 8
        mm_floor = tournament["mm_floor"] if "mm_floor" in tournament.keys() and tournament["mm_floor"] is not None else -30
        mm_zero = tournament["mm_zero"] if "mm_zero" in tournament.keys() and tournament["mm_zero"] is not None else 0
        pending_initial_scores = {
            row["id"]: mcmahon_score_from_rank(
                max(1, int(row["rank"] or 1)),
                bar=mm_bar,
                floor=mm_floor,
                zero=mm_zero,
            )
            for row in pending
        }

    rows.extend(
        {
            "id": None,
            "pending_id": row["id"],
            "player_id": -row["id"],
            "display_name": row["display_name"],
            "seed_rating": row["rating"],
            "seed_rank": row["rank"],
            "category": row["category"],
            "initial_score": pending_initial_scores.get(row["id"], 0.0),
            "acceleration": 0.0,
            "is_pending": True,
        }
        for row in pending
    )
    rows.sort(key=lambda r: (r["seed_rank"] or 0, str(r["display_name"] or "")))
    return rows


def _materialize_pending_players(conn, tournament_id, pending_id=None):
    """Materialize pending players into the tournament participants table."""
    """Return the number of pending players materialized."""
    pending_players = _list_pending_players(conn, tournament_id)
    if pending_id is not None:
        pending_players = [row for row in pending_players if row["id"] == pending_id]
    if not pending_players:
        return 0

    tournament_columns = _table_columns(conn, "tournaments")
    select_sql = "SELECT pairing_system"
    if {"mm_bar", "mm_floor", "mm_zero"}.issubset(tournament_columns):
        select_sql += ", mm_bar, mm_floor, mm_zero"
    select_sql += " FROM tournaments WHERE id = ?"
    tournament = conn.execute(select_sql, (tournament_id,)).fetchone()

    created = 0
    materialized_pending_ids = []
    for player_row in pending_players:
        display_name = player_row["display_name"]
        rating = player_row["rating"] or 0

        resolved_player_id = None
        if hasattr(player_row, "keys") and "resolved_player_id" in player_row.keys():
            resolved_player_id = player_row["resolved_player_id"]
        if resolved_player_id is not None:
            player_id = int(resolved_player_id)
            resolved_player = conn.execute(
                "SELECT display_name FROM players WHERE id = ?",
                (player_id,),
            ).fetchone()
            if resolved_player is None:
                raise ValueError("Resolved player not found")
            display_name = resolved_player["display_name"]
            conn.execute(
                """
                UPDATE tournament_pending_players
                SET display_name = ?
                WHERE tournament_id = ? AND id = ?
                """,
                (display_name, tournament_id, player_row["id"]),
            )
        else:
            suggested_name = player_row["suggested_name"]
            suggested_player = None
            if suggested_name:
                suggested_player = conn.execute(
                    "SELECT id, display_name FROM players WHERE active = 1 AND display_name = ?",
                    (suggested_name,),
                ).fetchone()
            if suggested_player is not None:
                player_id = suggested_player["id"]
                display_name = suggested_player["display_name"]
            else:
                player_id = ensure_player(conn, display_name, rating=rating, initial_rating=rating, active=1)
        if player_id is None:
            continue

        conn.execute(
            """
            UPDATE tournament_pairings
            SET white_player_id = ?, white_player_name = NULL
            WHERE round_id IN (SELECT id FROM tournament_rounds WHERE tournament_id = ?)
              AND white_player_id IS NULL
              AND white_player_name = ?
            """,
            (player_id, tournament_id, display_name),
        )
        conn.execute(
            """
            UPDATE tournament_pairings
            SET black_player_id = ?, black_player_name = NULL
            WHERE round_id IN (SELECT id FROM tournament_rounds WHERE tournament_id = ?)
              AND black_player_id IS NULL
              AND black_player_name = ?
            """,
            (player_id, tournament_id, display_name),
        )

        initial_score = 0.0
        if tournament and tournament["pairing_system"] == "mcmahon":
            mm_bar = tournament["mm_bar"] if "mm_bar" in tournament.keys() and tournament["mm_bar"] is not None else 8
            mm_floor = tournament["mm_floor"] if "mm_floor" in tournament.keys() and tournament["mm_floor"] is not None else -30
            mm_zero = tournament["mm_zero"] if "mm_zero" in tournament.keys() and tournament["mm_zero"] is not None else 30
            seed_rank = max(1, int(player_row["rank"] or 1))
            initial_score = mcmahon_score_from_rank(seed_rank, bar=mm_bar, floor=mm_floor, zero=mm_zero)

        conn.execute(
            """
            INSERT OR IGNORE INTO tournament_participants
                (tournament_id, player_id, seed_rating, seed_rank, category, initial_score, acceleration)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tournament_id,
                player_id,
                rating,
                player_row["rank"] or 0,
                player_row["category"] or "",
                initial_score,
                0,
            ),
        )
        created += 1
        materialized_pending_ids.append(player_row["id"])

    if tournament and tournament["pairing_system"] == "mcmahon":
        _recalculate_mcmahon_seeds(conn, tournament_id)

    if materialized_pending_ids:
        placeholders = ", ".join("?" for _ in materialized_pending_ids)
        conn.execute(
            f"DELETE FROM tournament_pending_players WHERE tournament_id = ? AND id IN ({placeholders})",
            (tournament_id, *materialized_pending_ids),
        )
    conn.commit()
    return created


def _mcmahon_category_strength(category):
    """Return a numeric strength value for a McMahon category string."""
    text = str(category or "").strip().upper()
    if not text:
        return 0.0
    if text.endswith("D"):
        try:
            return float(int(text[:-1]))
        except ValueError:
            return 0.0
    if text.endswith("K"):
        try:
            return -float(int(text[:-1]))
        except ValueError:
            return 0.0
    return 0.0


def _recalculate_mcmahon_seeds(conn, tournament_id):
    """Recalculate seed ranks and initial scores for all participants in a McMahon tournament."""
    tournament_columns = _table_columns(conn, "tournaments")
    participant_columns = _table_columns(conn, "tournament_participants")
    select_sql = "SELECT pairing_system, tournament_type"
    if {"mm_bar", "mm_floor", "mm_zero"}.issubset(tournament_columns):
        select_sql += ", mm_bar, mm_floor, mm_zero"
    select_sql += " FROM tournaments WHERE id = ?"
    tournament = conn.execute(select_sql, (tournament_id,)).fetchone()
    if tournament is None or tournament["pairing_system"] != "mcmahon":
        return

    if "mc_seeds_calculated" in participant_columns:
        pending = conn.execute(
            "SELECT COUNT(*) FROM tournament_participants WHERE tournament_id = ? AND mc_seeds_calculated = 0",
            (tournament_id,),
        ).fetchone()[0]
        if pending == 0:
            return

    mm_bar = tournament["mm_bar"] if "mm_bar" in tournament.keys() and tournament["mm_bar"] is not None else 8
    mm_floor = tournament["mm_floor"] if "mm_floor" in tournament.keys() and tournament["mm_floor"] is not None else -30
    mm_zero = tournament["mm_zero"] if "mm_zero" in tournament.keys() and tournament["mm_zero"] is not None else 0

    rows = conn.execute(
        """
        SELECT tp.id, tp.player_id, p.display_name, tp.seed_rating, tp.category
        FROM tournament_participants tp
        JOIN players p ON p.id = tp.player_id
        WHERE tp.tournament_id = ?
        """,
        (tournament_id,),
    ).fetchall()
    ordered = sorted(
        rows,
        key=lambda row: (
            -float(row["seed_rating"] or 0),
            -_mcmahon_category_strength(row["category"]),
            str(row["display_name"] or "").casefold(),
            int(row["player_id"]),
        ),
    )
    for seed_rank, row in enumerate(ordered, 1):
        initial_score = mcmahon_score_from_rank(seed_rank, bar=mm_bar, floor=mm_floor, zero=mm_zero)
        if "mc_seeds_calculated" in participant_columns:
            conn.execute(
                "UPDATE tournament_participants SET seed_rank = ?, initial_score = ?, mc_seeds_calculated = 1 WHERE id = ?",
                (seed_rank, initial_score, row["id"]),
            )
        else:
            conn.execute(
                "UPDATE tournament_participants SET seed_rank = ?, initial_score = ? WHERE id = ?",
                (seed_rank, initial_score, row["id"]),
            )


def _round_is_complete(conn, round_id):
    total_pairings = conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ? AND is_bye = 0",
        (round_id,),
    ).fetchone()[0]
    if total_pairings == 0:
        return True
    completed_pairings = conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ? AND is_bye = 0 AND result IN (?, ?, ?)",
        (round_id, *sorted(VALID_TOURNAMENT_RESULTS)),
    ).fetchone()[0]
    return completed_pairings == total_pairings


def _refresh_tournament_completion_state(conn, tournament_id, round_id=None):
    """Update tournament and round status based on completed pairings."""
    tournament = conn.execute(
        "SELECT rounds, status FROM tournaments WHERE id = ?",
        (tournament_id,),
    ).fetchone()
    if tournament is None:
        return

    if round_id is None:
        round_id = conn.execute(
            "SELECT id FROM tournament_rounds WHERE tournament_id = ? ORDER BY round_number DESC LIMIT 1",
            (tournament_id,),
        ).fetchone()
        if round_id is None:
            conn.execute("UPDATE tournaments SET status = 'draft' WHERE id = ?", (tournament_id,))
            conn.commit()
            return
        round_id = round_id[0]

    round_row = conn.execute(
        "SELECT round_number FROM tournament_rounds WHERE id = ? AND tournament_id = ?",
        (round_id, tournament_id),
    ).fetchone()
    if round_row is None:
        return

    played_pairings = conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ? AND is_bye = 0 AND result IN (?, ?, ?)",
        (round_id, *sorted(VALID_TOURNAMENT_RESULTS)),
    ).fetchone()[0]
    total_pairings = conn.execute(
        "SELECT COUNT(*) FROM tournament_pairings WHERE round_id = ? AND is_bye = 0",
        (round_id,),
    ).fetchone()[0]
    if total_pairings > 0:
        round_status = "completed" if played_pairings == total_pairings else "scheduled"
        conn.execute(
            "UPDATE tournament_rounds SET status = ? WHERE id = ?",
            (round_status, round_id),
        )

    all_rounds = conn.execute(
        "SELECT id, round_number, status FROM tournament_rounds WHERE tournament_id = ? ORDER BY round_number",
        (tournament_id,),
    ).fetchall()
    if not all_rounds:
        conn.execute("UPDATE tournaments SET status = 'draft' WHERE id = ?", (tournament_id,))
        conn.commit()
        return

    completed_rounds = 0
    for round_record in all_rounds:
        if round_record["status"] == "completed" or _round_is_complete(conn, round_record["id"]):
            completed_rounds += 1

    final_round_number = max(row["round_number"] for row in all_rounds)
    tournament_rounds_limit = int(tournament["rounds"] or 0)
    if tournament_rounds_limit and final_round_number >= tournament_rounds_limit and completed_rounds == len(all_rounds):
        conn.execute("UPDATE tournaments SET status = 'completed' WHERE id = ?", (tournament_id,))
    elif all_rounds:
        conn.execute("UPDATE tournaments SET status = 'active' WHERE id = ?", (tournament_id,))
    conn.commit()


def export_tournament_results(conn, tournament_id):
    """Return a tournament as an OpenGotha-compatible XML document."""
    tournament = conn.execute(
        "SELECT * FROM tournaments WHERE id = ?",
        (tournament_id,),
    ).fetchone()
    if tournament is None:
        raise ValueError("Tournament not found")

    participant_rows = conn.execute(
        """
        SELECT
            tp.player_id,
            tp.seed_rank,
            tp.seed_rating,
            tp.category,
            player.first_name,
            player.last_name,
            player.display_name,
            player.rating
        FROM tournament_participants tp
        JOIN players player ON player.id = tp.player_id
        WHERE tp.tournament_id = ?
        ORDER BY tp.seed_rank, tp.id
        """,
        (tournament_id,),
    ).fetchall()

    pending_rows = conn.execute(
        """
        SELECT id, display_name, rating, rank, category, source_key
        FROM tournament_pending_players
        WHERE tournament_id = ?
        ORDER BY rank, id
        """,
        (tournament_id,),
    ).fetchall()
    pairing_rows = conn.execute(
        """
        SELECT
            r.round_number,
            p.board_number,
            p.white_player_id,
            p.black_player_id,
            COALESCE(white.display_name, p.white_player_name) AS white_name,
            COALESCE(black.display_name, p.black_player_name) AS black_name,
            p.result,
            p.is_bye,
            p.handicap_stones
        FROM tournament_pairings p
        JOIN tournament_rounds r ON r.id = p.round_id
        LEFT JOIN players white ON white.id = p.white_player_id
        LEFT JOIN players black ON black.id = p.black_player_id
        WHERE r.tournament_id = ?
        ORDER BY r.round_number, p.board_number
        """,
        (tournament_id,),
    ).fetchall()

    root = ET.Element(
        "Tournament",
        {
            "dataVersion": "200",
            "gothaVersion": "300",
            "gothaMinorVersion": "00",
            "saveDT": datetime.now().strftime("%Y%m%d%H%M%S"),
            "externalIPAddress": "127.0.0.1",
            "remoteUUID": "00000000-0000-0000-0000-000000000000",
            "runningMode": "---",
            "fullVersionNumber": "---",
            "privateTournament": "false",
        },
    )

    player_keys = {}
    name_keys = {}
    used_keys = set()
    players_element = ET.SubElement(root, "Players")

    def add_player(player_id, display_name, rating, category, seed_rank, first_name=None, last_name=None, source_key=None):
        first_name = str(first_name or "").strip()
        last_name = str(last_name or "").strip()
        display_name = str(display_name or "").strip()
        if not first_name and not last_name and display_name:
            parts = display_name.split(" ", 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ""
        base_key = normalize_key(source_key or f"{last_name}{first_name}")
        base_key = base_key or f"player{player_id or len(used_keys) + 1}"
        player_key = base_key
        suffix = 2
        while player_key in used_keys:
            player_key = f"{base_key}{suffix}"
            suffix += 1
        used_keys.add(player_key)
        if player_id is not None:
            player_keys[player_id] = player_key
        if display_name:
            name_keys[normalize_key(display_name)] = player_key
        try:
            numeric_rating = float(rating or DEFAULT_RATING)
        except (TypeError, ValueError):
            numeric_rating = float(DEFAULT_RATING)
        rank_label = str(category or "").strip() or _category_for_rating(conn, numeric_rating)
        ET.SubElement(
            players_element,
            "Player",
            {
                "name": last_name,
                "firstName": first_name,
                "userName": "",
                "country": "",
                "club": "",
                "egfPin": "",
                "ffgLicence": "",
                "ffgLicenceStatus": "",
                "agaId": "",
                "agaExpirationDate": "",
                "rank": rank_label,
                "rating": f"{numeric_rating:g}",
                "ratingOrigin": "INI",
                "grade": rank_label,
                "smmsCorrection": "0",
                "participating": "1" * max(20, int(tournament["rounds"] or 1)),
                "registeringStatus": "FIN",
            },
        )

    for row in participant_rows:
        add_player(
            row["player_id"],
            row["display_name"],
            row["seed_rating"] or row["rating"],
            row["category"],
            row["seed_rank"],
            row["first_name"],
            row["last_name"],
        )
    for row in pending_rows:
        add_player(
            None,
            row["display_name"],
            row["rating"],
            row["category"],
            row["rank"],
            source_key=row["source_key"],
        )

    for row in pairing_rows:
        for player_id, player_name in (
            (row["white_player_id"], row["white_name"]),
            (row["black_player_id"], row["black_name"]),
        ):
            if player_id is not None and player_id not in player_keys:
                player = conn.execute(
                    "SELECT id, first_name, last_name, display_name, rating FROM players WHERE id = ?",
                    (player_id,),
                ).fetchone()
                if player is not None:
                    add_player(
                        player["id"],
                        player["display_name"],
                        player["rating"],
                        "",
                        0,
                        player["first_name"],
                        player["last_name"],
                    )
            elif player_id is None and normalize_key(player_name) not in name_keys:
                add_player(None, player_name, DEFAULT_RATING, "", 0)

    games_element = ET.SubElement(root, "Games")
    bye_rows = []
    result_codes = {
        "1-0": "RESULT_WHITEWINS",
        "0-1": "RESULT_BLACKWINS",
        "1/2-1/2": "RESULT_EQUAL",
    }
    for row in pairing_rows:
        white_key = player_keys.get(row["white_player_id"]) or name_keys.get(normalize_key(row["white_name"]))
        black_key = player_keys.get(row["black_player_id"]) or name_keys.get(normalize_key(row["black_name"]))
        if row["is_bye"]:
            if white_key:
                bye_rows.append((row["round_number"], white_key))
            continue
        if not white_key or not black_key:
            continue
        ET.SubElement(
            games_element,
            "Game",
            {
                "blackPlayer": black_key,
                "handicap": str(row["handicap_stones"] or 0),
                "knownColor": "true",
                "result": result_codes.get(row["result"], "RESULT_UNKNOWN"),
                "roundNumber": str(row["round_number"]),
                "tableNumber": str(row["board_number"]),
                "whitePlayer": white_key,
            },
        )

    bye_players_element = ET.SubElement(root, "ByePlayers")
    for round_number, player_key in bye_rows:
        ET.SubElement(
            bye_players_element,
            "ByePlayer",
            {"roundNumber": str(round_number), "player": player_key},
        )

    rounds = normalize_tournament_rounds(tournament["rounds"])
    bye_points = float(tournament["bye_points"] or 0)
    absent_points = float(tournament["absent_points"] or 0)
    tournament_columns = set(tournament.keys())
    mm_floor = tournament["mm_floor"] if "mm_floor" in tournament_columns else -30
    mm_bar = tournament["mm_bar"] if "mm_bar" in tournament_columns else 8
    mm_zero = tournament["mm_zero"] if "mm_zero" in tournament_columns else 30
    tournament_parameters = ET.SubElement(root, "TournamentParameterSet")
    general = ET.SubElement(
        tournament_parameters,
        "GeneralParameterSet",
        {
            "shortName": str(tournament["short_name"] or tournament["name"] or "tournament"),
            "name": str(tournament["name"] or ""),
            "location": str(tournament["location"] or ""),
            "description": str(tournament["description"] or "") if "description" in tournament.keys() else "",
            "director": "",
            "beginDate": str(tournament["begin_date"] or ""),
            "endDate": str(tournament["end_date"] or tournament["begin_date"] or ""),
            "bInternetGame": "false",
            "basicTime": "0",
            "complementaryTimeSystem": "SUDDENDEATH",
            "stdByoYomiTime": "30",
            "nbMovesCanTime": "15",
            "canByoYomiTime": "300",
            "fischerTime": "0",
            "size": "19",
            "komi": "7.5",
            "numberOfRounds": str(rounds),
            "numberOfCategories": "1",
            "numberOfBZHGroups": "1",
            "genMMFloor": _rank_label(mm_floor, "30K"),
            "genMMBar": _rank_label(mm_bar, "9D"),
            "genMMZero": str(mm_zero if mm_zero is not None else 30),
            "genNBW2ValueAbsent": str(round(absent_points * 2)),
            "genNBW2ValueBye": str(round(bye_points * 2)),
            "genMMS2ValueAbsent": str(round(absent_points * 2)),
            "genMMS2ValueBye": str(round(bye_points * 2)),
            "genRoundDownNBWMMS": "true",
            "genCountNotPlayedGamesAsHalfPoint": "false",
        },
    )
    categories = ET.SubElement(general, "Categories")
    ET.SubElement(categories, "Category", {"number": "1", "lowerLimit": "30K"})
    bzh_groups = ET.SubElement(general, "BZHGroups")
    ET.SubElement(bzh_groups, "BZHGroup", {"number": "1", "lowerLimit": "30K"})

    handicap_enabled = bool(tournament["handicap_enabled"] or 0) if "handicap_enabled" in tournament.keys() else True
    ET.SubElement(
        tournament_parameters,
        "HandicapParameterSet",
        {
            "hdBase": "RANK",
            "hdNoHdRankThreshold": "1D",
            "hdCorrection": "0",
            "hdCeiling": "9" if handicap_enabled else "0",
        },
    )

    placement = ET.SubElement(tournament_parameters, "PlacementParameterSet")
    placement_criteria = [
        criterion.strip() for criterion in str(tournament["placement_criteria"] or "NBW").split(",") if criterion.strip()
    ] or ["NBW"]
    criteria_element = ET.SubElement(placement, "PlacementCriteria")
    for number, criterion in enumerate(placement_criteria, 1):
        ET.SubElement(criteria_element, "PlacementCriterion", {"number": str(number), "name": criterion})

    pairing_system = str(tournament["pairing_system"] or "swiss")
    ET.SubElement(
        tournament_parameters,
        "PairingParameterSet",
        {
            "paiStandardNX1Factor": "0.5",
            "paiBaAvoidDuplGame": "500000000000000",
            "paiBaRandom": "0",
            "paiBaDeterministic": "true",
            "paiBaBalanceWB": "1000000",
            "paiMaAvoidMixingCategories": "0",
            "paiMaMinimizeScoreDifference": "100000000000",
            "paiMaDUDDWeight": "100000000",
            "paiMaCompensateDUDD": "true",
            "paiMaDUDDUpperMode": "MID",
            "paiMaDUDDLowerMode": "MID",
            "paiMaMaximizeSeeding": "5000000",
            "paiMaLastRoundForSeedSystem1": "2",
            "paiMaSeedSystem1": "SPLITANDSLIP" if pairing_system == "accelerated_swiss" else "SPLITANDFOLD",
            "paiMaSeedSystem2": "SPLITANDFOLD",
            "paiMaAdditionalPlacementCritSystem1": "RATING",
            "paiMaAdditionalPlacementCritSystem2": "NULL",
            "paiSeBarThresholdActive": "true",
            "paiSeRankThreshold": "4D",
            "paiSeNbWinsThresholdActive": "true",
            "paiSeDefSecCrit": "100000000000",
            "paiSeMinimizeHandicap": "0",
            "paiSeAvoidSameGeo": "0",
            "paiSePreferMMSDiffRatherThanSameCountry": "0",
            "paiSePreferMMSDiffRatherThanSameClubsGroup": "0",
            "paiSePreferMMSDiffRatherThanSameClub": "0",
        },
    )
    ET.SubElement(
        tournament_parameters,
        "DPParameterSet",
        {
            "playerSortType": "name",
            "gameFormat": "full",
            "showPlayerGrade": "true",
            "showPlayerCountry": "false",
            "showPlayerClub": "true",
            "showByePlayer": "true",
            "showNotPairedPlayers": "true",
            "showNotParticipatingPlayers": "false",
            "showNotFinallyRegisteredPlayers": "false",
            "displayNPPlayers": "false",
            "displayNumCol": "true",
            "displayPlCol": "true",
            "displayCoCol": "true",
            "displayClCol": "true",
            "displayIndGamesInMatches": "true",
        },
    )
    ET.SubElement(
        tournament_parameters,
        "PublishParameterSet",
        {"print": "true", "exportToLocalFile": "true", "htmlAutoScroll": "false"},
    )

    ET.indent(root, space="  ")
    xml_body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return "\ufeff<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"no\"?>\n" + xml_body + "\n"


def create_tournament_from_gotha(
    conn, xml_path, pairing_system=None, player_decisions=None, metadata_overrides=None
):
    """Create a new tournament from OpenGotha XML metadata."""
    from app import repair_legacy_players_table

    repair_legacy_players_table(conn)
    xml_path = _resolve_gotha_path(xml_path)
    metadata = read_gotha_tournament(xml_path)
    metadata.update(metadata_overrides or {})
    pairing_system = normalize_tournament_system(pairing_system or metadata["pairing_system"])
    if pairing_system not in SUPPORTED_SYSTEMS:
        raise ValueError(f"Unknown pairing system: {pairing_system}")
    metadata.update({"pairing_system": pairing_system, "tournament_type": pairing_system})

    duplicate_tournament = conn.execute(
        """
        SELECT id
        FROM tournaments
        WHERE lower(name) = lower(?) AND COALESCE(begin_date, '') = COALESCE(?, '')
        ORDER BY id DESC LIMIT 1
        """,
        (metadata["name"], metadata.get("begin_date")),
    ).fetchone()
    if duplicate_tournament is not None:
        raise ValueError("Tournament with the same name and start date already exists")

    tournament_columns = _table_columns(conn, "tournaments")
    insert_columns = [
        "name", "short_name", "location", "begin_date", "end_date", "rounds",
        "tournament_type", "pairing_system", "bye_points", "absent_points",
        "placement_criteria",
    ]
    insert_values = [
        metadata["name"], metadata["short_name"], metadata["location"],
        metadata["begin_date"], metadata["end_date"], normalize_tournament_rounds(metadata["rounds"]),
        metadata["tournament_type"], pairing_system,
        metadata["bye_points"], metadata["absent_points"],
        metadata["placement_criteria"],
    ]
    if "description" in tournament_columns:
        insert_columns.insert(3, "description")
        insert_values.insert(3, metadata.get("description", ""))
    if {"mm_bar", "mm_floor", "mm_zero"}.issubset(tournament_columns):
        insert_columns.extend(["mm_bar", "mm_floor", "mm_zero"])
        insert_values.extend([
            metadata.get("mm_bar", 8), metadata.get("mm_floor", -30), metadata.get("mm_zero", 30),
        ])
    if "acceleration_scheme" in tournament_columns:
        insert_columns.append("acceleration_scheme")
        insert_values.append(metadata.get("acceleration_scheme") or DEFAULT_ACCELERATION_SCHEME)
    if "acceleration_rounds" in tournament_columns:
        insert_columns.append("acceleration_rounds")
        insert_values.append(int(metadata.get("acceleration_rounds") or DEFAULT_ACCELERATION_ROUNDS))
    if "category_rounds" in tournament_columns:
        insert_columns.append("category_rounds")
        insert_values.append(int(metadata.get("category_rounds") or DEFAULT_CATEGORY_ROUNDS))
    if "handicap_enabled" in tournament_columns:
        has_handicap_game = any(
            int(game.get("handicap") or 0) > 0
            for game in ET.parse(xml_path).getroot().findall("Games/Game")
            if str(game.get("handicap") or "0").strip().lstrip("-").isdigit()
        )
        insert_columns.append("handicap_enabled")
        insert_values.append(1 if has_handicap_game else 0)
    insert_columns.extend(["status", "source_format", "created_at"])
    insert_values.extend(["draft", "OpenGotha XML", current_timestamp()])

    placeholders = ", ".join("?" for _ in insert_values)
    conn.execute(
        f"INSERT INTO tournaments ({', '.join(insert_columns)}) VALUES ({placeholders})",
        insert_values,
    )
    tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    lookup = _player_lookup(conn)
    matched = 0

    for participant in metadata["players"]:
        source_key = participant["key"]
        decision = (player_decisions or {}).get(source_key, "auto")
        if decision == "reject":
            continue
        player = lookup.get(participant["key"])
        if decision not in ("auto", "new"):
            try:
                selected_id = int(decision)
            except (TypeError, ValueError) as exc:
                raise ValueError("Invalid player reconciliation decision") from exc
            player = conn.execute(
                "SELECT id, rating FROM players WHERE id = ? AND active = 1",
                (selected_id,),
            ).fetchone()
            if player is None:
                raise ValueError("Selected player does not exist")
        if player is None:
            if decision == "new":
                player_id = ensure_player(
                    conn,
                    participant["display_name"],
                    rating=participant["rating"],
                    initial_rating=participant["rating"],
                    active=1,
                )
                player = conn.execute(
                    "SELECT id, rating FROM players WHERE id = ?", (player_id,)
                ).fetchone()
            else:
                suggested = _suggest_player_name(participant["display_name"], conn)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO tournament_pending_players
                        (tournament_id, display_name, suggested_name, rating, rank, category, source_key, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tournament_id, participant["display_name"], suggested,
                     participant["rating"], participant["rank"], participant["category"], source_key,
                     current_timestamp()),
                )
                continue

            lookup[source_key] = player

        rank = participant["rank"]
        if pairing_system == "mcmahon":
            initial_score = mcmahon_score_from_rank(
                rank,
                bar=metadata.get("mm_bar", 8),
                floor=metadata.get("mm_floor", -30),
                zero=metadata.get("mm_zero", 30),
            )
        else:
            initial_score = 0
        acceleration = (
            acceleration_for_rank(
                rank,
                len(metadata["players"]),
                scheme=metadata.get("acceleration_scheme"),
                player_rank=rank,
            )
            if pairing_system == "accelerated_swiss"
            else 0
        )
        seed_rating = player["rating"] if player["rating"] is not None else (participant["rating"] or 0)
        participant_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tournament_participants)").fetchall()}
        if pairing_system == "mcmahon" and "mc_seeds_calculated" in participant_columns:
            conn.execute(
                """
                INSERT OR IGNORE INTO tournament_participants
                    (tournament_id, player_id, seed_rating, seed_rank, category, initial_score, acceleration, mc_seeds_calculated)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (tournament_id, player["id"], seed_rating, rank, participant["category"], initial_score, acceleration),
            )
        else:
            conn.execute(
                """
                INSERT OR IGNORE INTO tournament_participants
                    (tournament_id, player_id, seed_rating, seed_rank, category, initial_score, acceleration)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (tournament_id, player["id"], seed_rating, rank, participant["category"], initial_score, acceleration),
            )
        matched += 1

    root = ET.parse(xml_path).getroot()
    participant_names = {participant["key"]: participant["display_name"] for participant in metadata["players"]}
    round_ids = {}
    for game in root.findall("Games/Game"):
        round_number = int(game.get("roundNumber") or 0)
        if round_number <= 0:
            continue
        round_id = round_ids.get(round_number)
        if round_id is None:
            conn.execute(
                "INSERT INTO tournament_rounds (tournament_id, round_number, status) VALUES (?, ?, 'completed')",
                (tournament_id, round_number),
            )
            round_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            round_ids[round_number] = round_id

        white_key = normalize_key(game.get("whitePlayer") or "")
        black_key = normalize_key(game.get("blackPlayer") or "")
        white_player = lookup.get(white_key)
        black_player = lookup.get(black_key)
        white_name = (white_player["display_name"] if white_player else None) or participant_names.get(white_key)
        black_name = (black_player["display_name"] if black_player else None) or participant_names.get(black_key)
        if white_name is None and black_name is None:
            continue

        result_code = game.get("result")
        if result_code == "RESULT_WHITEWINS":
            result = "1-0"
        elif result_code == "RESULT_BLACKWINS":
            result = "0-1"
        elif result_code == "RESULT_EQUAL":
            result = "1/2-1/2"
        else:
            continue

        board_number = game.get("tableNumber")
        if board_number is None or board_number == "":
            board_number = conn.execute(
                "SELECT COALESCE(MAX(board_number), 0) + 1 FROM tournament_pairings WHERE round_id = ?",
                (round_id,),
            ).fetchone()[0]
        else:
            board_number = int(board_number)

        is_bye = white_player is not None and black_player is None and not black_name

        try:
            handicap_stones = int(game.get("handicap") or 0)
        except (TypeError, ValueError):
            handicap_stones = 0

        conn.execute(
            """
            INSERT INTO tournament_pairings
                (round_id, board_number, white_player_id, black_player_id, white_player_name, black_player_name, result, is_bye, handicap_stones)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                round_id,
                board_number,
                white_player["id"] if white_player else None,
                black_player["id"] if black_player else None,
                white_name if white_player is None else None,
                black_name if black_player is None else None,
                result,
                int(is_bye),
                handicap_stones,
            ),
        )
        if is_bye:
            conn.execute(
                "INSERT OR REPLACE INTO tournament_round_players (round_id, player_id, status) VALUES (?, ?, 'bye')",
                (round_id, white_player["id"]),
            )
            conn.execute(
                "UPDATE tournament_participants SET received_bye = 1 WHERE tournament_id = ? AND player_id = ?",
                (tournament_id, white_player["id"]),
            )

    _refresh_tournament_completion_state(conn, tournament_id)
    # Imported rounds may already be complete in OpenGotha, but the imported
    # tournament stays Draft until an administrator processes its results.
    conn.execute(
        "UPDATE tournaments SET status = 'draft' WHERE id = ?",
        (tournament_id,),
    )
    conn.commit()
    return tournament_id, metadata.to_dict(), matched


def _participant_state(conn, tournament_id, acceleration_scheme=None, acceleration_active=False):
    participants = conn.execute(
        "SELECT * FROM tournament_participants WHERE tournament_id = ? ORDER BY seed_rank, id",
        (tournament_id,),
    ).fetchall()
    previous = conn.execute(
        """
         SELECT p.white_player_id, p.black_player_id, p.result, p.is_bye,
             r.round_number
        FROM tournament_pairings p
        JOIN tournament_rounds r ON r.id = p.round_id
        WHERE r.tournament_id = ?
        """,
        (tournament_id,),
    ).fetchall()
    settings = conn.execute(
        "SELECT bye_points, absent_points FROM tournaments WHERE id = ?",
        (tournament_id,),
    ).fetchone()
    bye_points = float(settings["bye_points"] if settings and settings["bye_points"] is not None else 1.0)
    absent_points = float(settings["absent_points"] if settings and settings["absent_points"] is not None else 0.0)
    seed_order = sorted(
        participants,
        key=lambda row: (-float(row["seed_rating"] or 0), row["player_id"]),
    )
    seed_ranks = {row["player_id"]: rank for rank, row in enumerate(seed_order, 1)}
    state = {
        row["player_id"]: {
            "id": row["player_id"],
            "rating": row["seed_rating"],
            "score": 0.0,
            "initial_score": row["initial_score"],
            "acceleration": (
                acceleration_for_rank(
                    seed_ranks[row["player_id"]],
                    len(participants),
                    scheme=acceleration_scheme,
                    player_rank=round(category_value(row["seed_rating"] or DEFAULT_RATING)),
                )
                if acceleration_active
                else 0.0
            ),
            "category": row["category"],
            "opponents": set(),
            "colors": {"white": 0, "black": 0},
            "received_bye": bool(row["received_bye"]),
        }
        for row in participants
    }
    for row in previous:
        white = row["white_player_id"]
        black = row["black_player_id"]
        if white not in state:
            continue
        if row["is_bye"] or black is None:
            state[white]["received_bye"] = True
            state[white]["score"] += bye_points
            continue
        if black not in state:
            continue
        state[white]["opponents"].add(black)
        state[black]["opponents"].add(white)
        state[white]["colors"]["white"] += 1
        state[black]["colors"]["black"] += 1
        if row["result"] == "1-0":
            state[white]["score"] += 1.0
        elif row["result"] == "0-1":
            state[black]["score"] += 1.0
        elif row["result"] == "1/2-1/2":
            state[white]["score"] += 0.5
            state[black]["score"] += 0.5

    absent_players = conn.execute(
        """
        SELECT rp.player_id
        FROM tournament_round_players rp
        JOIN tournament_rounds r ON r.id = rp.round_id
        WHERE r.tournament_id = ? AND rp.status = 'absent'
        """,
        (tournament_id,),
    ).fetchall()
    for row in absent_players:
        if row["player_id"] in state:
            state[row["player_id"]]["score"] += absent_points
    return state


def generate_next_round(conn, tournament_id):
    tournament = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    if tournament is None:
        raise ValueError("Tournament not found")
    round_number = conn.execute(
        "SELECT COALESCE(MAX(round_number), 0) + 1 FROM tournament_rounds WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone()[0]
    source_format = tournament["source_format"] if "source_format" in tournament.keys() else None
    if tournament["rounds"] and round_number > tournament["rounds"] and source_format != "OpenGotha XML":
        raise ValueError("All tournament rounds have already been generated")
    if tournament["rounds"] and round_number > tournament["rounds"]:
        raise ValueError("All tournament rounds have already been generated")

    pairing_policy = _pairing_policy(tournament, round_number)
    state = _participant_state(
        conn,
        tournament_id,
        acceleration_scheme=(
            tournament["acceleration_scheme"]
            if "acceleration_scheme" in tournament.keys()
            else None
        ),
        acceleration_active=pairing_policy["acceleration_active"],
    )
    if len(state) < 2:
        raise ValueError("At least two tournament players are required")
    pairings = pair_players(
        list(state.values()),
        tournament["pairing_system"],
        category_strict=pairing_policy["category_strict"],
    )
    conn.execute(
        "INSERT INTO tournament_rounds (tournament_id, round_number, status) VALUES (?, ?, 'scheduled')",
        (tournament_id, round_number),
    )
    round_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for board_number, pairing in enumerate(pairings, 1):
        pairing_handicap = (
            0
            if pairing["is_bye"]
            else _auto_handicap_stones(conn, pairing["white_player_id"], pairing["black_player_id"])
        )
        conn.execute(
            """
            INSERT INTO tournament_pairings
                (round_id, board_number, white_player_id, black_player_id, is_bye, handicap_stones)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                round_id,
                board_number,
                pairing["white_player_id"],
                pairing["black_player_id"],
                int(pairing["is_bye"]),
                pairing_handicap,
            ),
        )
        if pairing["is_bye"]:
            conn.execute(
                "INSERT OR REPLACE INTO tournament_round_players (round_id, player_id, status) VALUES (?, ?, 'bye')",
                (round_id, pairing["white_player_id"]),
            )
            conn.execute(
                "UPDATE tournament_participants SET received_bye = 1 WHERE tournament_id = ? AND player_id = ?",
                (tournament_id, pairing["white_player_id"]),
            )
        else:
            conn.executemany(
                "INSERT OR REPLACE INTO tournament_round_players (round_id, player_id, status) VALUES (?, ?, 'paired')",
                [(round_id, pairing["white_player_id"]), (round_id, pairing["black_player_id"])],
            )
    conn.execute("UPDATE tournaments SET status = 'active' WHERE id = ?", (tournament_id,))
    _refresh_tournament_completion_state(conn, tournament_id, round_id)
    conn.commit()
    return round_id, pairings


def set_round_player_status(conn, tournament_id, round_id, player_id, status):
    if status not in {"bye", "absent"}:
        raise ValueError("Invalid round player status")
    valid_round = conn.execute(
        "SELECT 1 FROM tournament_rounds WHERE id = ? AND tournament_id = ?",
        (round_id, tournament_id),
    ).fetchone()
    valid_player = conn.execute(
        "SELECT 1 FROM tournament_participants WHERE tournament_id = ? AND player_id = ?",
        (tournament_id, player_id),
    ).fetchone()
    if not valid_round or not valid_player:
        raise ValueError("Round player not found")
    paired = conn.execute(
        "SELECT 1 FROM tournament_pairings WHERE round_id = ? AND (white_player_id = ? OR black_player_id = ?)",
        (round_id, player_id, player_id),
    ).fetchone()
    if paired:
        raise ValueError("Player is already paired in this round")

    occupied = conn.execute(
        "SELECT 1 FROM tournament_round_players WHERE round_id = ? AND player_id = ?",
        (round_id, player_id),
    ).fetchone()
    if occupied:
        raise ValueError("Player already has a status in this round")
    conn.execute(
        "INSERT INTO tournament_round_players (round_id, player_id, status) VALUES (?, ?, ?)",
        (round_id, player_id, status),
    )
    if status == "bye":
        conn.execute(
            "INSERT INTO tournament_pairings (round_id, board_number, white_player_id, black_player_id, is_bye) VALUES (?, (SELECT COALESCE(MAX(board_number), 0) + 1 FROM tournament_pairings WHERE round_id = ?), ?, NULL, 1)",
            (round_id, round_id, player_id),
        )
        conn.execute(
            "UPDATE tournament_participants SET received_bye = 1 WHERE tournament_id = ? AND player_id = ?",
            (tournament_id, player_id),
        )
    conn.commit()


def pair_selected_players(conn, tournament_id, round_id, player_ids):
    """Pair selected players; if nothing is selected, pair the remaining unpaired players."""
    selected = list(dict.fromkeys(int(player_id) for player_id in player_ids if player_id not in (None, "")))
    if selected:
        marked_players = {
            row[0]
            for row in conn.execute(
                "SELECT player_id FROM tournament_round_players WHERE round_id = ? AND player_id IN ({})".format(
                    ",".join("?" for _ in selected)
                ),
                (round_id, *selected),
            ).fetchall()
        }
        selected = [player_id for player_id in selected if player_id not in marked_players]

    if not selected:
        participants = conn.execute(
            """
            SELECT tp.player_id
            FROM tournament_participants tp
            LEFT JOIN tournament_pairings p
                ON p.round_id = ?
               AND (p.white_player_id = tp.player_id OR p.black_player_id = tp.player_id)
            LEFT JOIN tournament_round_players rrp
                ON rrp.round_id = ?
               AND rrp.player_id = tp.player_id
            WHERE tp.tournament_id = ?
              AND p.id IS NULL
              AND rrp.player_id IS NULL
            ORDER BY tp.seed_rank, tp.player_id
            """,
            (round_id, round_id, tournament_id),
        ).fetchall()
        selected = [row["player_id"] for row in participants]

    if not selected:
        return

    if len(selected) == 1:
        set_round_player_status(conn, tournament_id, round_id, selected[0], "bye")
        return

    tournament = conn.execute(
        "SELECT * FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()
    current_round = conn.execute(
        "SELECT round_number FROM tournament_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0]
    pairing_policy = _pairing_policy(tournament, current_round)
    state = _participant_state(
        conn,
        tournament_id,
        acceleration_scheme=(
            tournament["acceleration_scheme"]
            if "acceleration_scheme" in tournament.keys()
            else None
        ),
        acceleration_active=pairing_policy["acceleration_active"],
    )
    players = [state[player_id] for player_id in selected if player_id in state]
    # Route through pair_players so already-played opponents are avoided
    # wherever a valid pairing exists, instead of pairing by seed order alone.
    pairings = pair_players(
        players,
        tournament["pairing_system"],
        category_strict=pairing_policy["category_strict"],
    )
    for pairing in pairings:
        if pairing["is_bye"]:
            set_round_player_status(conn, tournament_id, round_id, pairing["white_player_id"], "bye")
        else:
            manual_pair(conn, tournament_id, round_id, pairing["white_player_id"], pairing["black_player_id"])


def add_participant(conn, tournament_id, player_id):
    tournament_columns = _table_columns(conn, "tournaments")
    select_sql = "SELECT pairing_system, tournament_type"
    if "acceleration_scheme" in tournament_columns:
        select_sql += ", acceleration_scheme"
    if {"mm_bar", "mm_floor", "mm_zero"}.issubset(tournament_columns):
        select_sql += ", mm_bar, mm_floor, mm_zero"
    select_sql += " FROM tournaments WHERE id = ?"
    tournament = conn.execute(select_sql, (tournament_id,)).fetchone()
    player = conn.execute(
        "SELECT id, rating FROM players WHERE id = ? AND active = 1", (player_id,)
    ).fetchone()
    if tournament is None or player is None:
        raise ValueError("Tournament player not found")
    existing = conn.execute(
        "SELECT 1 FROM tournament_participants WHERE tournament_id = ? AND player_id = ?",
        (tournament_id, player_id),
    ).fetchone()
    if existing:
        raise ValueError("Player is already in this tournament")

    participant_count = conn.execute(
        "SELECT COUNT(*) FROM tournament_participants WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone()[0]

    if tournament["pairing_system"] == "mcmahon":
        participant_columns = _table_columns(conn, "tournament_participants")
        category = _category_for_rating(conn, player["rating"])
        if {"mm_bar", "mm_floor", "mm_zero"}.issubset(tournament_columns):
            participant_count += 1
            mm_zero = int(tournament["mm_zero"] or 0)
            if mm_zero < participant_count:
                conn.execute(
                    "UPDATE tournaments SET mm_zero = ? WHERE id = ?",
                    (participant_count, tournament_id),
                )
        if "mc_seeds_calculated" in participant_columns:
            conn.execute(
                """
                INSERT INTO tournament_participants
                    (tournament_id, player_id, seed_rating, category, initial_score, acceleration, mc_seeds_calculated)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (tournament_id, player_id, player["rating"] or 0, category, 0.0, 0.0),
            )
        else:
            conn.execute(
                """
                INSERT INTO tournament_participants
                    (tournament_id, player_id, seed_rating, category, initial_score, acceleration)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (tournament_id, player_id, player["rating"] or 0, category, 0.0, 0.0),
            )
        _recalculate_mcmahon_seeds(conn, tournament_id)
        conn.commit()
        return

    seed_rank = participant_count + 1
    initial_score = 0.0
    acceleration = 0.0

    conn.execute(
        """
        INSERT INTO tournament_participants
            (tournament_id, player_id, seed_rating, seed_rank, category, initial_score, acceleration)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (tournament_id, player_id, player["rating"] or 0, seed_rank, _category_for_rating(conn, player["rating"]), initial_score, acceleration),
    )
    conn.commit()


def remove_participant(conn, tournament_id, player_id):
    tournament = conn.execute(
        "SELECT pairing_system FROM tournaments WHERE id = ?",
        (tournament_id,),
    ).fetchone()
    pairing = conn.execute(
        """
        SELECT 1
        FROM tournament_pairings p
        JOIN tournament_rounds r ON r.id = p.round_id
        WHERE r.tournament_id = ?
          AND (p.white_player_id = ? OR p.black_player_id = ?)
        """,
        (tournament_id, player_id, player_id),
    ).fetchone()
    if pairing:
        raise ValueError("Cannot remove a player after they have been paired")
    deleted = conn.execute(
        "DELETE FROM tournament_participants WHERE tournament_id = ? AND player_id = ?",
        (tournament_id, player_id),
    ).rowcount
    if not deleted:
        raise ValueError("Tournament player not found")
    if tournament and tournament["pairing_system"] == "mcmahon":
        _recalculate_mcmahon_seeds(conn, tournament_id)
    conn.commit()


def manual_pair(conn, tournament_id, round_id, white_player_id, black_player_id, handicap_stones=None):
    """Creates a pairing. handicap_stones defaults to an auto-suggested
    value from the two players' current ratings (see _auto_handicap_stones);
    pass an explicit int (including 0) to override it, e.g. from a
    tournament director editing the suggestion in the admin UI.
    """
    round_row = conn.execute(
        "SELECT id FROM tournament_rounds WHERE id = ? AND tournament_id = ?",
        (round_id, tournament_id),
    ).fetchone()
    if round_row is None or white_player_id == black_player_id:
        raise ValueError("Invalid manual pairing")
    participant_ids = {
        row[0]
        for row in conn.execute(
            "SELECT player_id FROM tournament_participants WHERE tournament_id = ?",
            (tournament_id,),
        ).fetchall()
    }
    if white_player_id not in participant_ids or black_player_id not in participant_ids:
        raise ValueError("Both players must be in the tournament")

    already_marked = conn.execute(
        """
        SELECT player_id FROM tournament_round_players
        WHERE round_id = ? AND player_id IN (?, ?)
        """,
        (round_id, white_player_id, black_player_id),
    ).fetchall()
    if already_marked:
        raise ValueError("A player is already marked for this round")

    occupied = conn.execute(
        """
        SELECT 1 FROM tournament_pairings
        WHERE round_id = ? AND (white_player_id IN (?, ?) OR black_player_id IN (?, ?))
        """,
        (round_id, white_player_id, black_player_id, white_player_id, black_player_id),
    ).fetchone()
    if occupied:
        raise ValueError("A player is already paired in this round")
    used_boards = {
        row[0]
        for row in conn.execute(
            "SELECT board_number FROM tournament_pairings WHERE round_id = ?",
            (round_id,),
        ).fetchall()
    }
    board_number = 1
    while board_number in used_boards:
        board_number += 1

    if handicap_stones is None and _tournament_handicap_enabled(conn, tournament_id):
        handicap_stones = _auto_handicap_stones(conn, white_player_id, black_player_id)
    if handicap_stones is None:
        handicap_stones = 0

    conn.execute(
        """
        INSERT INTO tournament_pairings
            (round_id, board_number, white_player_id, black_player_id, is_bye, handicap_stones)
        VALUES (?, ?, ?, ?, 0, ?)
        """,
        (round_id, board_number, white_player_id, black_player_id, handicap_stones),
    )
    conn.commit()


def update_pairing(conn, tournament_id, pairing_id, white_player_id, black_player_id):
    pairing = conn.execute(
        """
        SELECT p.round_id, p.white_player_id AS old_white_player_id,
               p.black_player_id AS old_black_player_id
        FROM tournament_pairings p
        JOIN tournament_rounds r ON r.id = p.round_id
        WHERE p.id = ? AND r.tournament_id = ? AND p.is_bye = 0
        """,
        (pairing_id, tournament_id),
    ).fetchone()
    if pairing is None or white_player_id == black_player_id:
        raise ValueError("Invalid pairing")

    participant_ids = {
        row[0]
        for row in conn.execute(
            "SELECT player_id FROM tournament_participants WHERE tournament_id = ?",
            (tournament_id,),
        ).fetchall()
    }
    if white_player_id not in participant_ids or black_player_id not in participant_ids:
        raise ValueError("Both players must be in the tournament")

    occupied = conn.execute(
        """
        SELECT 1 FROM tournament_pairings
        WHERE round_id = ? AND id != ?
          AND (white_player_id IN (?, ?) OR black_player_id IN (?, ?))
        """,
        (pairing["round_id"], pairing_id, white_player_id, black_player_id, white_player_id, black_player_id),
    ).fetchone()
    if occupied:
        raise ValueError("A player is already paired in this round")

    conn.execute(
        """
        UPDATE tournament_pairings
        SET white_player_id = ?, black_player_id = ?
        WHERE id = ?
        """,
        (white_player_id, black_player_id, pairing_id),
    )
    conn.execute(
        """
        DELETE FROM tournament_round_players
        WHERE round_id = ? AND player_id IN (?, ?)
        """,
        (pairing["round_id"], pairing["old_white_player_id"], pairing["old_black_player_id"]),
    )
    conn.executemany(
        "INSERT OR REPLACE INTO tournament_round_players (round_id, player_id, status) VALUES (?, ?, 'paired')",
        [(pairing["round_id"], white_player_id), (pairing["round_id"], black_player_id)],
    )
    sync_pairing_match(conn, tournament_id, pairing_id)
    _refresh_tournament_completion_state(conn, tournament_id, pairing["round_id"])
    conn.commit()


def sync_match_pairing(conn, match_id, white_player_id, black_player_id, result, handicap_stones):
    """Propagate editable match fields back to its tournament pairing."""
    ensure_tournament_match_identity(conn)
    pairing_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('tournament_pairings', 'tournament_rounds')"
        ).fetchall()
    }
    if len(pairing_tables) != 2:
        return None
    pairing = conn.execute(
        """
        SELECT p.id, p.round_id, r.tournament_id
        FROM matches m
        JOIN tournament_pairings p ON p.id = m.tournament_pairing_id
        JOIN tournament_rounds r ON r.id = p.round_id
        WHERE m.id = ?
        """,
        (match_id,),
    ).fetchone()
    if pairing is None:
        return None
    if result not in VALID_TOURNAMENT_RESULTS:
        raise ValueError("Invalid tournament result")
    participant_ids = {
        row[0]
        for row in conn.execute(
            "SELECT player_id FROM tournament_participants WHERE tournament_id = ?",
            (pairing["tournament_id"],),
        ).fetchall()
    }
    if (
        white_player_id == black_player_id
        or white_player_id not in participant_ids
        or black_player_id not in participant_ids
    ):
        raise ValueError("Both players must be in the tournament")
    occupied = conn.execute(
        """
        SELECT 1 FROM tournament_pairings
        WHERE round_id = ? AND id != ?
          AND (white_player_id IN (?, ?) OR black_player_id IN (?, ?))
        """,
        (pairing["round_id"], pairing["id"], white_player_id, black_player_id, white_player_id, black_player_id),
    ).fetchone()
    if occupied:
        raise ValueError("A player is already paired in this round")
    conn.execute(
        """
        UPDATE tournament_pairings
        SET white_player_id = ?, black_player_id = ?, result = ?, handicap_stones = ?
        WHERE id = ?
        """,
        (white_player_id, black_player_id, result, handicap_stones or 0, pairing["id"]),
    )
    _refresh_tournament_completion_state(conn, pairing["tournament_id"], pairing["round_id"])
    return pairing["tournament_id"]


def update_pairing_handicap(conn, tournament_id, pairing_id, handicap_stones):
    """Lets a tournament director override the auto-suggested handicap for
    an existing pairing. If the pairing has been materialized, the linked
    match is updated as well.
    """
    if handicap_stones is None:
        raise ValueError("handicap_stones is required")
    try:
        handicap_stones = int(handicap_stones)
    except (TypeError, ValueError):
        raise ValueError("handicap_stones must be an integer")
    if not (0 <= handicap_stones <= 9):
        raise ValueError("handicap_stones must be between 0 and 9")

    updated = conn.execute(
        """
        UPDATE tournament_pairings
        SET handicap_stones = ?
        WHERE id = ? AND round_id IN (
            SELECT id FROM tournament_rounds WHERE tournament_id = ?
        )
        """,
        (handicap_stones, pairing_id, tournament_id),
    ).rowcount
    if not updated:
        raise ValueError("Pairing not found")
    sync_pairing_match(conn, tournament_id, pairing_id)
    conn.commit()


def unpair(conn, tournament_id, pairing_id):
    pairing = conn.execute(
        "SELECT round_id, white_player_id, black_player_id FROM tournament_pairings WHERE id = ?",
        (pairing_id,),
    ).fetchone()
    deleted = conn.execute(
        """
        DELETE FROM tournament_pairings
        WHERE id = ? AND round_id IN (
            SELECT id FROM tournament_rounds WHERE tournament_id = ?
        )
        """,
        (pairing_id, tournament_id),
    ).rowcount
    if not deleted:
        raise ValueError("Pairing not found")
    if pairing:
        conn.execute(
            "DELETE FROM tournament_round_players WHERE round_id = ? AND player_id IN (?, ?)",
            (pairing["round_id"], pairing["white_player_id"], pairing["black_player_id"]),
        )
        ensure_tournament_match_identity(conn)
        conn.execute(
            "DELETE FROM matches WHERE tournament_pairing_id = ?",
            (pairing_id,),
        )
        _refresh_tournament_completion_state(conn, tournament_id, pairing["round_id"])
    conn.commit()


def sync_pairing_match(conn, tournament_id, pairing_id):
    """Keep the materialized match for a pairing synchronized with its source."""
    ensure_tournament_match_identity(conn)
    pairing = conn.execute(
        """
        SELECT p.id, p.white_player_id, p.black_player_id, p.result,
               p.is_bye, p.handicap_stones, r.round_number, t.name,
               t.begin_date, t.end_date
        FROM tournament_pairings p
        JOIN tournament_rounds r ON r.id = p.round_id
        JOIN tournaments t ON t.id = r.tournament_id
        WHERE p.id = ? AND r.tournament_id = ?
        """,
        (pairing_id, tournament_id),
    ).fetchone()
    if pairing is None:
        raise ValueError("Pairing not found")

    existing = conn.execute(
        "SELECT id, match_date, event, notes, round_number FROM matches WHERE tournament_pairing_id = ?",
        (pairing_id,),
    ).fetchone()
    valid_game = (
        not pairing["is_bye"]
        and pairing["white_player_id"] is not None
        and pairing["black_player_id"] is not None
        and pairing["result"] in VALID_TOURNAMENT_RESULTS
    )
    if not valid_game:
        if existing is not None:
            conn.execute("DELETE FROM matches WHERE id = ?", (existing["id"],))
        return False

    match_date = (
        existing["match_date"]
        if existing is not None
        else pairing["begin_date"] or pairing["end_date"] or current_date().isoformat()
    )
    event = (
        existing["event"]
        if existing is not None and existing["event"]
        else pairing["name"]
    )
    notes = (
        existing["notes"]
        if existing is not None and existing["notes"] is not None
        else str(pairing["round_number"])
    )
    match_round = (
        existing["round_number"]
        if existing is not None and existing["round_number"] is not None
        else pairing["round_number"]
    )
    handicap_stones = pairing["handicap_stones"] or 0
    if existing is None:
        conn.execute(
            """
            INSERT INTO matches
                (match_date, white_player_id, black_player_id, result, event,
                 notes, round_number, tournament_pairing_id, handicap_stones)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match_date,
                pairing["white_player_id"],
                pairing["black_player_id"],
                pairing["result"],
                event,
                notes,
                match_round,
                pairing_id,
                handicap_stones,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE matches
            SET match_date = ?, white_player_id = ?, black_player_id = ?,
                result = ?, event = ?, notes = ?, round_number = ?,
                handicap_stones = ?
            WHERE id = ?
            """,
            (
                match_date,
                pairing["white_player_id"],
                pairing["black_player_id"],
                pairing["result"],
                event,
                notes,
                match_round,
                handicap_stones,
                existing["id"],
            ),
        )
    return True


def sync_tournament_matches(conn, tournament_id, name=None, match_date=None):
    """Propagate edited tournament metadata to all materialized matches."""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'matches'"
    ).fetchone() is None:
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
        UPDATE matches AS m
        SET {', '.join(fields)}
        WHERE m.tournament_pairing_id IN (
            SELECT p.id
            FROM tournament_pairings p
            JOIN tournament_rounds r ON r.id = p.round_id
            WHERE r.tournament_id = ?
        )
        """,
        values,
    ).rowcount


def save_tournament_matches(conn, tournament_id):
    """Materialize and synchronize every completed pairing in a tournament."""
    tournament = conn.execute(
        "SELECT id FROM tournaments WHERE id = ?",
        (tournament_id,),
    ).fetchone()
    if tournament is None:
        raise ValueError("Tournament not found")

    pairing_ids = conn.execute(
        """
        SELECT p.id
        FROM tournament_pairings p
        JOIN tournament_rounds r ON r.id = p.round_id
        WHERE r.tournament_id = ?
        ORDER BY r.round_number, p.board_number
        """,
        (tournament_id,),
    ).fetchall()
    saved = 0
    for pairing in pairing_ids:
        if sync_pairing_match(conn, tournament_id, pairing["id"]):
            saved += 1
    _refresh_tournament_completion_state(conn, tournament_id)
    return saved


def set_pairing_result(conn, tournament_id, pairing_id, result):
    pairing = conn.execute(
        """
        SELECT p.id, p.is_bye, p.round_id, p.result
        FROM tournament_pairings p
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
        """
        UPDATE tournament_pairings
        SET result = ?
        WHERE id = ? AND round_id IN (
            SELECT id FROM tournament_rounds WHERE tournament_id = ?
        )
        """,
        (result or None, pairing_id, tournament_id),
    ).rowcount
    if not updated:
        raise ValueError("Pairing not found")
    sync_pairing_match(conn, tournament_id, pairing_id)
    _refresh_tournament_completion_state(conn, tournament_id, pairing["round_id"])
    conn.commit()


def process_tournament_round_matches(conn, tournament_id, round_id=None, match_date=None, event=None):
    """Persist completed non-bye tournament pairings into the main matches table."""
    ensure_tournament_match_identity(conn)
    if round_id is None:
        round_id = conn.execute(
            "SELECT id FROM tournament_rounds WHERE tournament_id = ? ORDER BY round_number DESC LIMIT 1",
            (tournament_id,),
        ).fetchone()
        if round_id is None:
            raise ValueError("Round not found")
        round_id = round_id[0]

    round_row = conn.execute(
        "SELECT id, round_number FROM tournament_rounds WHERE id = ? AND tournament_id = ?",
        (round_id, tournament_id),
    ).fetchone()
    if round_row is None:
        raise ValueError("Round not found")

    if match_date is None:
        match_date = conn.execute(
            "SELECT COALESCE(begin_date, end_date, ?) FROM tournaments WHERE id = ?",
            (current_date().isoformat(), tournament_id),
        ).fetchone()
        match_date = match_date[0] if match_date else current_date().isoformat()

    tournament = conn.execute(
        "SELECT name, rounds FROM tournaments WHERE id = ?",
        (tournament_id,),
    ).fetchone()
    if tournament is not None:
        tournament_name = tournament["name"] if "name" in tournament.keys() else getattr(tournament, "name", "")
        tournament_rounds_limit = tournament["rounds"] if "rounds" in tournament.keys() else None
    else:
        tournament_name = ""
        tournament_rounds_limit = None
    event_name = event or (f"{tournament_name} Round {round_row['round_number']}" if tournament_name else "Tournament round")

    inserted = 0
    _materialize_pending_players(conn, tournament_id)

    pairings = conn.execute(
        """
        SELECT id, white_player_id, black_player_id, result, handicap_stones
        FROM tournament_pairings
        WHERE round_id = ? AND is_bye = 0 AND result IS NOT NULL AND result != ''
        """,
        (round_id,),
    ).fetchall()

    for pairing in pairings:
        if pairing["white_player_id"] is None or pairing["black_player_id"] is None:
            continue
        if pairing["result"] not in VALID_TOURNAMENT_RESULTS:
            continue
        if round_row["round_number"] <= 0:
            continue
        if tournament_rounds_limit is not None and round_row["round_number"] > tournament_rounds_limit:
            continue

        pairing_handicap = pairing["handicap_stones"] if "handicap_stones" in pairing.keys() and pairing["handicap_stones"] is not None else 0

        existing = conn.execute(
            "SELECT id, match_date, result, event, handicap_stones FROM matches WHERE tournament_pairing_id = ?",
            (pairing["id"],),
        ).fetchone()
        if existing is not None:
            existing_handicap = existing["handicap_stones"] if "handicap_stones" in existing.keys() and existing["handicap_stones"] is not None else 0
            if (existing["match_date"], existing["result"], existing["event"], existing_handicap) != (
                match_date,
                pairing["result"],
                event_name,
                pairing_handicap,
            ):
                conn.execute(
                    """
                    UPDATE matches
                    SET match_date = ?, result = ?, event = ?, notes = ?, round_number = ?, handicap_stones = ?
                    WHERE id = ?
                    """,
                    (
                        match_date,
                        pairing["result"],
                        event_name,
                        round_row["round_number"],
                        round_row["round_number"],
                        pairing_handicap,
                        existing["id"],
                    ),
                )
            continue

        conn.execute(
            """
            INSERT INTO matches
                (match_date, white_player_id, black_player_id, result, event, notes, round_number, tournament_pairing_id, handicap_stones)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match_date,
                pairing["white_player_id"],
                pairing["black_player_id"],
                pairing["result"],
                event_name,
                round_row["round_number"],
                round_row["round_number"],
                pairing["id"],
                pairing_handicap,
            ),
        )
        inserted += 1

    _refresh_tournament_completion_state(conn, tournament_id, round_id)
    return inserted


def get_tournament_standings(conn, tournament_id):
    tournament = conn.execute(
        "SELECT * FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()
    if tournament is None:
        raise ValueError("Tournament not found")
    participant_rows = list_tournament_participants(conn, tournament_id)
    pending_id_by_name = {
        row["display_name"]: row["player_id"] for row in participant_rows if row["is_pending"]
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
        white_id = row["white_player_id"]
        if white_id is None:
            white_id = pending_id_by_name.get(row["white_player_name"])
        black_id = row["black_player_id"]
        if black_id is None:
            black_id = pending_id_by_name.get(row["black_player_name"])
        games.append(
            {
                "white_player_id": white_id,
                "black_player_id": black_id,
                "result": row["result"],
                "is_bye": row["is_bye"],
                "round_number": row["round_number"],
            }
        )
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
    games = games + [
        {
            "white_player_id": row["white_player_id"],
            "black_player_id": row["black_player_id"],
            "result": row["result"],
            "is_bye": row["is_bye"],
            "is_absent": row["is_absent"],
            "round_number": row["round_number"],
        }
        for row in absent_rows
    ]
    scored_rounds = [
        game["round_number"]
        for game in games
        if game.get("result") or game.get("is_bye") or game.get("is_absent")
    ]
    current_round = max(scored_rounds, default=0)
    acceleration_rounds = (
        int(tournament["acceleration_rounds"])
        if "acceleration_rounds" in tournament.keys() and tournament["acceleration_rounds"] is not None
        else DEFAULT_ACCELERATION_ROUNDS
    )
    acceleration_active = (
        tournament["pairing_system"] == "accelerated_swiss"
        and 1 <= current_round <= acceleration_rounds
    )
    if tournament["pairing_system"] == "accelerated_swiss":
        seed_order = sorted(
            players,
            key=lambda player: (-float(player["rating"] or 0), player["id"]),
        )
        for seed_rank, player in enumerate(seed_order, 1):
            player["acceleration"] = (
                acceleration_for_rank(
                    seed_rank,
                    len(players),
                    scheme=(
                        tournament["acceleration_scheme"]
                        if "acceleration_scheme" in tournament.keys()
                        else None
                    ),
                    player_rank=round(category_value(player["rating"] or DEFAULT_RATING)),
                )
                if acceleration_active
                else 0.0
            )
    settings = conn.execute(
        "SELECT bye_points, absent_points FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()
    return calculate_standings(
        players,
        games,
        tournament["pairing_system"],
        bye_points=settings["bye_points"] if settings else 1.0,
        absent_points=settings["absent_points"] if settings else 0.0,
    )


def delete_tournament(conn, tournament_id):
    """Delete a tournament and all of its dependent records atomically."""
    from app import repair_legacy_players_table

    repair_legacy_players_table(conn)
    tournament = conn.execute(
        "SELECT id FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()
    if tournament is None:
        raise ValueError("Tournament not found")

    try:
        conn.execute(
            """
            DELETE FROM tournament_pairings
            WHERE round_id IN (
                SELECT id FROM tournament_rounds WHERE tournament_id = ?
            )
            """,
            (tournament_id,),
        )
        conn.execute(
            """
            DELETE FROM tournament_round_players
            WHERE round_id IN (
                SELECT id FROM tournament_rounds WHERE tournament_id = ?
            )
            """,
            (tournament_id,),
        )
        conn.execute(
            "DELETE FROM tournament_rounds WHERE tournament_id = ?",
            (tournament_id,),
        )
        conn.execute(
            "DELETE FROM tournament_participants WHERE tournament_id = ?",
            (tournament_id,),
        )
        conn.execute(
            "DELETE FROM tournament_pending_players WHERE tournament_id = ?",
            (tournament_id,),
        )
        conn.execute("DELETE FROM tournaments WHERE id = ?", (tournament_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
