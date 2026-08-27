"""Tournament persistence and OpenGotha-compatible metadata import."""

import csv
from difflib import SequenceMatcher
from io import StringIO
from pathlib import Path
import xml.etree.ElementTree as ET

from config import DEFAULT_RATING, GLICKO_K, GLICKO_M
from services.category_service import glicko_to_category
from services.common import current_date, current_timestamp
from services.helpers import normalize_key, normalize_text
from services.import_gotha import GothaPlayer, GothaTournamentPayload
from services.pairing_service import (
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


def normalize_tournament_rounds(rounds):
    """Tournament rounds must always be valid and non-zero."""
    try:
        value = int(rounds)
    except (TypeError, ValueError):
        return 1
    return max(1, value)


def _table_columns(conn, table_name):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


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
        tournament_type = "mcmahon"
        pairing_system = "mcmahon"
    elif explicit_pairing_system == "swiss_cat":
        tournament_type = "swiss_cat"
        pairing_system = "swiss_cat"
    elif explicit_pairing_system in {"swiss", "accelerated_swiss"}:
        tournament_type = "swiss"
        pairing_system = explicit_pairing_system
    else:
        first_criterion = placement_names[0] if placement_names else "NBW"
        if first_criterion == "MMS":
            tournament_type = "mcmahon"
        elif first_criterion == "CAT":
            tournament_type = "swiss_cat"
        else:
            tournament_type = "swiss"
        pairing_system = "mcmahon" if tournament_type == "mcmahon" else (
            "swiss_cat" if tournament_type == "swiss_cat" else "swiss"
        )
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
        bye_points=(mms2_bye if tournament_type == "mcmahon" else nbw2_bye) / 2,
        absent_points=(mms2_absent if tournament_type == "mcmahon" else nbw2_absent) / 2,
        mm_bar=mm_bar_value,
        mm_floor=mm_floor_value,
        mm_zero=mm_zero_value,
        placement_criteria=",".join(
            criterion.get("name", "NULL") for criterion in placement_criteria
        ),
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
    """Return tournament pairings as a CSV string for export."""
    tournament = conn.execute(
        "SELECT id, name FROM tournaments WHERE id = ?",
        (tournament_id,),
    ).fetchone()
    if tournament is None:
        raise ValueError("Tournament not found")

    rows = conn.execute(
        """
        SELECT
            r.round_number,
            p.board_number,
            p.white_player_id,
            p.black_player_id,
            COALESCE(white.display_name, p.white_player_name) AS white_name,
            COALESCE(black.display_name, p.black_player_name) AS black_name,
            p.result,
            p.is_bye
        FROM tournament_pairings p
        JOIN tournament_rounds r ON r.id = p.round_id
        LEFT JOIN players white ON white.id = p.white_player_id
        LEFT JOIN players black ON black.id = p.black_player_id
        WHERE r.tournament_id = ?
        ORDER BY r.round_number, p.board_number
        """,
        (tournament_id,),
    ).fetchall()

    output = StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow([
        "tournament_id",
        "tournament_name",
        "round_number",
        "board_number",
        "white_player_id",
        "black_player_id",
        "white_player",
        "black_player",
        "result",
        "is_bye",
    ])
    for row in rows:
        writer.writerow([
            tournament_id,
            tournament["name"],
            row["round_number"],
            row["board_number"],
            row["white_player_id"],
            row["black_player_id"],
            row["white_name"],
            row["black_name"] or "",
            row["result"] or "",
            row["is_bye"],
        ])
    return output.getvalue()


def create_tournament_from_gotha(
    conn, xml_path, pairing_system=None, player_decisions=None, metadata_overrides=None
):
    """Create a new tournament from OpenGotha XML metadata."""
    from app import repair_legacy_players_table

    repair_legacy_players_table(conn)
    xml_path = _resolve_gotha_path(xml_path)
    metadata = read_gotha_tournament(xml_path)
    metadata.update(metadata_overrides or {})
    pairing_system = pairing_system or metadata["pairing_system"]
    if pairing_system not in SUPPORTED_SYSTEMS:
        raise ValueError(f"Unknown pairing system: {pairing_system}")

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
    if {"mm_bar", "mm_floor", "mm_zero"}.issubset(tournament_columns):
        insert_columns.extend(["mm_bar", "mm_floor", "mm_zero"])
        insert_values.extend([
            metadata.get("mm_bar", 8), metadata.get("mm_floor", -30), metadata.get("mm_zero", 30),
        ])
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
        acceleration = acceleration_for_rank(rank, len(metadata["players"])) if pairing_system == "accelerated_swiss" else 0
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

        conn.execute(
            """
            INSERT INTO tournament_pairings
                (round_id, board_number, white_player_id, black_player_id, white_player_name, black_player_name, result, is_bye)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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


def _participant_state(conn, tournament_id):
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
    state = {
        row["player_id"]: {
            "id": row["player_id"],
            "rating": row["seed_rating"],
            "score": row["score"],
            "initial_score": row["initial_score"],
            "acceleration": row["acceleration"],
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
            continue
        if black not in state:
            continue
        state[white]["opponents"].add(black)
        state[black]["opponents"].add(white)
        state[white]["colors"]["white"] += 1
        state[black]["colors"]["black"] += 1
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

    state = _participant_state(conn, tournament_id)
    if len(state) < 2:
        raise ValueError("At least two tournament players are required")
    pairings = pair_players(list(state.values()), tournament["pairing_system"])
    conn.execute(
        "INSERT INTO tournament_rounds (tournament_id, round_number, status) VALUES (?, ?, 'scheduled')",
        (tournament_id, round_number),
    )
    round_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for board_number, pairing in enumerate(pairings, 1):
        conn.execute(
            """
            INSERT INTO tournament_pairings
                (round_id, board_number, white_player_id, black_player_id, is_bye)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                round_id,
                board_number,
                pairing["white_player_id"],
                pairing["black_player_id"],
                int(pairing["is_bye"]),
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
        "SELECT pairing_system FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()
    state = _participant_state(conn, tournament_id)
    players = [state[player_id] for player_id in selected if player_id in state]
    # Route through pair_players so already-played opponents are avoided
    # wherever a valid pairing exists, instead of pairing by seed order alone.
    pairings = pair_players(players, tournament["pairing_system"])
    for pairing in pairings:
        if pairing["is_bye"]:
            set_round_player_status(conn, tournament_id, round_id, pairing["white_player_id"], "bye")
        else:
            manual_pair(conn, tournament_id, round_id, pairing["white_player_id"], pairing["black_player_id"])


def add_participant(conn, tournament_id, player_id):
    tournament_columns = _table_columns(conn, "tournaments")
    select_sql = "SELECT pairing_system, tournament_type"
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
    if tournament["pairing_system"] == "accelerated_swiss":
        initial_score = 0.0
        acceleration = acceleration_for_rank(seed_rank, participant_count + 1)
    else:
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


def manual_pair(conn, tournament_id, round_id, white_player_id, black_player_id):
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
    conn.execute(
        """
        INSERT INTO tournament_pairings
            (round_id, board_number, white_player_id, black_player_id, is_bye)
        VALUES (?, ?, ?, ?, 0)
        """,
        (round_id, board_number, white_player_id, black_player_id),
    )
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
    conn.commit()


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
        SELECT id, white_player_id, black_player_id, result
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

        existing = conn.execute(
            "SELECT id, match_date, result, event FROM matches WHERE tournament_pairing_id = ?",
            (pairing["id"],),
        ).fetchone()
        if existing is not None:
            if (existing["match_date"], existing["result"], existing["event"]) != (
                match_date,
                pairing["result"],
                event_name,
            ):
                conn.execute(
                    """
                    UPDATE matches
                    SET match_date = ?, result = ?, event = ?, notes = ?, round_number = ?
                    WHERE id = ?
                    """,
                    (
                        match_date,
                        pairing["result"],
                        event_name,
                        round_row["round_number"],
                        round_row["round_number"],
                        existing["id"],
                    ),
                )
            continue

        conn.execute(
            """
            INSERT INTO matches
                (match_date, white_player_id, black_player_id, result, event, notes, round_number, tournament_pairing_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        inserted += 1

    _refresh_tournament_completion_state(conn, tournament_id, round_id)
    return inserted


def get_tournament_standings(conn, tournament_id):
    tournament = conn.execute(
        "SELECT tournament_type FROM tournaments WHERE id = ?", (tournament_id,)
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
    games = games + list(absent_rows)
    settings = conn.execute(
        "SELECT bye_points, absent_points FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()
    return calculate_standings(
        players,
        games,
        tournament["tournament_type"],
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
