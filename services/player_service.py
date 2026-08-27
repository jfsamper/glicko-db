# services/player_service.py
"""Service for managing player records, lookups, and related operations."""
from datetime import date
import math
import sqlite3

from services.common import build_rating_chart_data, current_date, get_db, TRANSLATIONS
from services.home_stats import build_player_badges
from services.player_stats import build_recent_result_summaries
from services.helpers import normalize_key, normalize_text, slugify, split_name
from services.category_service import get_category_config, glicko_to_category
from services.standings_service import calculate_standings


def get_player_rank_badge(conn, player_id):
    player = conn.execute(
        "SELECT id, rating, games_played FROM players WHERE id = ?",
        (player_id,),
    ).fetchone()
    if player is None or player["games_played"] is None or player["games_played"] < 1:
        return None

    ranked_player = conn.execute(
        """
        SELECT player_rank
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (
                       ORDER BY COALESCE(rating, 0) DESC, id ASC
                   ) AS player_rank
            FROM players
            WHERE games_played IS NOT NULL AND games_played >= 1
        )
        WHERE id = ?
        """,
        (player_id,),
    ).fetchone()

    if ranked_player is None or ranked_player["player_rank"] > 5:
        return None
    return ranked_player["player_rank"]


from config import DEFAULT_RATING, DEFAULT_RD, DEFAULT_VOLATILITY, GLICKO_M


def parse_rating_filter(value):
    """Return a finite rating bound, or ``None`` for an invalid filter."""
    if value in (None, ""):
        return None

    try:
        rating = float(value)
    except (TypeError, ValueError):
        return None

    return rating if math.isfinite(rating) else None


def _normalize_player_rating(value):
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return DEFAULT_RATING
    if not math.isfinite(rating) or rating <= 0:
        return DEFAULT_RATING
    return max(rating, float(GLICKO_M))


def build_player_lookup(conn):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(players)").fetchall()}
    query_columns = ["id", "display_name"]
    if "slug" in columns:
        query_columns.append("slug")
    rows = conn.execute(f"SELECT {', '.join(query_columns)} FROM players").fetchall()
    lookup = {}
    for row in rows:
        display_name = row["display_name"]
        if display_name:
            lookup[normalize_key(display_name)] = row["id"]
        if "slug" in row.keys() and row["slug"]:
            lookup[normalize_key(row["slug"])] = row["id"]
    return lookup


def ensure_player(conn, display_name, rating=None, initial_rating=None, active=None, player_lookup=None):
    if not display_name:
        return None

    display_name = normalize_text(display_name)
    if not display_name:
        return None

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(players)").fetchall()}
    has_slug = "slug" in columns
    has_first_name = "first_name" in columns
    has_last_name = "last_name" in columns
    has_country = "country" in columns
    has_club = "club" in columns
    has_initial_rating = "initial_rating" in columns
    has_rating = "rating" in columns
    has_rd = "rd" in columns
    has_volatility = "volatility" in columns
    has_active = "active" in columns

    base_slug = slugify(display_name)
    slug = base_slug
    key = normalize_key(display_name)
    normalized_rating = _normalize_player_rating(rating)
    normalized_initial_rating = (
        _normalize_player_rating(initial_rating)
        if initial_rating is not None
        else None
    )

    if player_lookup is None:
        player_lookup = build_player_lookup(conn)

    if key in player_lookup:
        existing_id = player_lookup[key]
        row = conn.execute(
            "SELECT id, display_name" + (", slug" if has_slug else "") + " FROM players WHERE id = ?",
            (existing_id,),
        ).fetchone()
        if row is not None:
            if rating is not None and has_rating:
                update_sql = "UPDATE players SET display_name = ?"
                params: list[object] = [display_name]
                if has_slug:
                    update_sql += ", slug = ?"
                    params.append(row["slug"] or slug)
                if has_rating:
                    update_sql += ", rating = ?"
                    params.append(normalized_rating)
                if has_initial_rating:
                    update_sql += ", initial_rating = COALESCE(initial_rating, ?)"
                    params.append(normalized_initial_rating or normalized_rating)
                if active is not None and has_active:
                    update_sql += ", active = ?"
                    params.append(int(active))
                update_sql += " WHERE id = ?"
                params.append(row["id"])
                conn.execute(update_sql, params)
            elif has_slug:
                conn.execute(
                    "UPDATE players SET display_name = ?, slug = ? WHERE id = ?",
                    (display_name, row["slug"] or slug, row["id"]),
                )
            else:
                conn.execute(
                    "UPDATE players SET display_name = ? WHERE id = ?",
                    (display_name, row["id"]),
                )

            player_lookup[key] = row["id"]
            if has_slug and row["slug"]:
                player_lookup[normalize_key(row["slug"])] = row["id"]
            return row["id"]

    if has_slug:
        counter = 2
        while conn.execute("SELECT 1 FROM players WHERE slug = ?", (slug,)).fetchone():
            slug = f"{base_slug}-{counter}"
            counter += 1

    first_name, last_name = split_name(display_name)

    if has_slug and has_first_name and has_last_name and has_country and has_club and has_initial_rating and has_rating and has_rd and has_volatility:
        insert_sql = "INSERT INTO players (first_name, last_name, display_name, country, club, slug, initial_rating, rating, rd, volatility"
        insert_values = [first_name, last_name, display_name, "COL", "", slug, normalized_initial_rating or normalized_rating, normalized_rating, DEFAULT_RD, DEFAULT_VOLATILITY]
        if has_active:
            insert_sql += ", active"
            insert_values.append(int(active) if active is not None else 1)
        insert_sql += ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?"
        if has_active:
            insert_sql += ", ?"
        insert_sql += ")"
        conn.execute(insert_sql, insert_values)
    elif has_first_name and has_last_name and has_rating:
        insert_sql = "INSERT INTO players (first_name, last_name, display_name, rating"
        insert_values = [first_name, last_name, display_name, normalized_rating]
        if has_active:
            insert_sql += ", active"
            insert_values.append(int(active) if active is not None else 1)
        insert_sql += ") VALUES (?, ?, ?, ?"
        if has_active:
            insert_sql += ", ?"
        insert_sql += ")"
        conn.execute(insert_sql, insert_values)
    else:
        insert_sql = "INSERT INTO players (display_name, rating"
        insert_values = [display_name, normalized_rating]
        if has_active:
            insert_sql += ", active"
            insert_values.append(int(active) if active is not None else 1)
        insert_sql += ") VALUES (?, ?"
        if has_active:
            insert_sql += ", ?"
        insert_sql += ")"
        conn.execute(insert_sql, insert_values)
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    player_lookup[key] = new_id
    if has_slug:
        player_lookup[normalize_key(slug)] = new_id
    return new_id


def _player_result_record(player_id, match):
    if match["white_player_id"] == player_id:
        if match["result"] == "1-0":
            return (1, 0, 0)
        if match["result"] == "0-1":
            return (0, 1, 0)
        return (0, 0, 1)

    if match["result"] == "1-0":
        return (0, 1, 0)
    if match["result"] == "0-1":
        return (1, 0, 0)
    return (0, 0, 1)


def _summarize_matches_for_period(conn, player_id, start_date=None, end_date=None):
    result = {"wins": 0, "losses": 0, "draws": 0}
    clauses = ["(white_player_id = ? OR black_player_id = ?)"]
    params = [player_id, player_id]

    if start_date is not None and end_date is not None:
        clauses.append("match_date BETWEEN ? AND ?")
        params.extend([start_date.isoformat(), end_date.isoformat()])
    elif start_date is not None:
        clauses.append("match_date >= ?")
        params.append(start_date.isoformat())
    elif end_date is not None:
        clauses.append("match_date <= ?")
        params.append(end_date.isoformat())

    matches = conn.execute(
        f"SELECT white_player_id, black_player_id, result FROM matches WHERE {' AND '.join(clauses)} ORDER BY match_date DESC, id DESC",
        params,
    ).fetchall()

    for match in matches:
        wins, losses, draws = _player_result_record(player_id, match)
        result["wins"] += wins
        result["losses"] += losses
        result["draws"] += draws

    return result


def load_player(
    player_id, lang="es", page=1, page_size=25, season=None, category=None,
    tournament_page=1, tournament_page_size=10,
):
    conn = get_db()
    player = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()

    if not player:
        conn.close()
        return None

    page = parse_page_number(page, default=1)
    page_size = parse_page_size(page_size, default=25)
    offset = (page - 1) * page_size
    season = str(season or "").strip()
    if not (season.isdigit() and len(season) == 4):
        season = ""
    match_condition = "m.white_player_id = ? OR m.black_player_id = ?"
    match_params = [player["id"], player["id"]]
    if season:
        match_condition = f"({match_condition}) AND m.match_date GLOB ?"
        match_params.append(f"{season}-??-??")
    count_condition = match_condition.replace("m.", "")

    columns = conn.execute("PRAGMA table_info(matches)").fetchall()
    has_event = any(column[1] == "event" for column in columns)

    total_matches = conn.execute(
        f"SELECT COUNT(*) FROM matches WHERE {count_condition}",
        match_params,
    ).fetchone()[0]

    match_select = """
        SELECT m.match_date, m.result, {event_select},
               m.white_player_id, m.black_player_id,
               p_white.display_name AS white_name,
               p_white.slug AS white_slug,
               p_white.rating AS white_rating,
               p_black.display_name AS black_name,
               p_black.slug AS black_slug,
               p_black.rating AS black_rating
        FROM matches m
        JOIN players p_white ON p_white.id = m.white_player_id
        JOIN players p_black ON p_black.id = m.black_player_id
        WHERE {match_condition}
        ORDER BY m.match_date DESC, m.id DESC
    """
    if has_event:
        match_event_select = "m.event"
    else:
        match_event_select = "NULL AS event"

    matches = conn.execute(
        match_select.format(event_select=match_event_select, match_condition=match_condition) + " LIMIT ? OFFSET ?",
        (*match_params, page_size, offset),
    ).fetchall()
    all_matches = conn.execute(
        match_select.format(event_select=match_event_select, match_condition=match_condition) + " LIMIT -1 OFFSET 0",
        match_params,
    ).fetchall()
    white_stats = {"games": 0, "wins": 0, "losses": 0, "draws": 0}
    black_stats = {"games": 0, "wins": 0, "losses": 0, "draws": 0}
    for match in all_matches:
        stats = white_stats if match["white_player_id"] == player["id"] else black_stats
        stats["games"] += 1
        result = match["result"]
        if result == "1-0" and stats is white_stats or result == "0-1" and stats is black_stats:
            stats["wins"] += 1
        elif result == "0-1" and stats is white_stats or result == "1-0" and stats is black_stats:
            stats["losses"] += 1
        else:
            stats["draws"] += 1
    
    #
    opponent_records = {}

    for match in all_matches:
        if match["white_player_id"] == player["id"]:
            opponent_name = match["black_name"]
            opponent_id = match["black_player_id"]

            opponent_rating = match["black_rating"]

            is_win = match["result"] == "1-0"
            is_loss = match["result"] == "0-1"

        else:
            opponent_name = match["white_name"]
            opponent_id = match["white_player_id"]

            opponent_rating = match["white_rating"]

            is_win = match["result"] == "0-1"
            is_loss = match["result"] == "1-0"

        if opponent_name not in opponent_records:
            opponent_records[opponent_name] = {
                "name": opponent_name,
                "id": opponent_id,
                "rating": opponent_rating,
                "wins": 0,
                "losses": 0,
                "draws": 0,
            }

        if is_win:
            opponent_records[opponent_name]["wins"] += 1
        elif is_loss:
            opponent_records[opponent_name]["losses"] += 1
        else:
            opponent_records[opponent_name]["draws"] += 1

    snapshots = conn.execute(
        "SELECT snapshot_date, rating, rd, volatility FROM rating_snapshots WHERE player_id = ? ORDER BY snapshot_date, id",
        (player["id"],),
    ).fetchall()

    translations = TRANSLATIONS.get(lang, TRANSLATIONS["es"])

    badges = build_player_badges(player["id"], translations=translations, conn=conn)
    rank_badge = get_player_rank_badge(conn, player["id"])
    if rank_badge is not None:
        badges.append({
            "label": f"#{rank_badge}",
            "value": None,
            "period": [translations["period_current"]],
            "is_rank_badge": True,
        })

    today = current_date()
    yearly_start = date(today.year, 1, 1)
    yearly_end = date(today.year, 12, 31)
    quarter = (today.month - 1) // 3 + 1
    start_month = (quarter - 1) * 3 + 1
    end_month = quarter * 3
    from calendar import monthrange
    yearly_results = _summarize_matches_for_period(conn, player["id"], yearly_start, yearly_end)
    quarter_start = date(today.year, start_month, 1)
    quarter_end = date(today.year, end_month, monthrange(today.year, end_month)[1])
    quarterly_results = _summarize_matches_for_period(conn, player["id"], quarter_start, quarter_end)
    total_results = {"wins": 0, "losses": 0, "draws": 0}
    for match in all_matches:
        wins, losses, draws = _player_result_record(player["id"], match)
        total_results["wins"] += wins
        total_results["losses"] += losses
        total_results["draws"] += draws

    total_games = (total_results["wins"] + total_results["losses"] + total_results["draws"])
    white_games = (white_stats["wins"] or 0) + (white_stats["losses"] or 0) + (white_stats["draws"] or 0)
    black_games = (black_stats["wins"] or 0) + (black_stats["losses"] or 0) + (black_stats["draws"] or 0)

    overall_win_rate = (total_results["wins"] / total_games * 100) if total_games else 0
    white_win_rate = (white_stats["wins"] / white_games * 100) if white_games else 0
    black_win_rate = (black_stats["wins"] / black_games * 100) if black_games else 0

    recent_streak = []
    for match in all_matches[:10]:
        result_key = match["result"]
        if match["white_player_id"] == player["id"]:
            if result_key == "1-0":
                recent_streak.append("X")
            elif result_key == "0-1":
                recent_streak.append("O")
            else:
                recent_streak.append("D")
        else:
            if result_key == "0-1":
                recent_streak.append("X")
            elif result_key == "1-0":
                recent_streak.append("O")
            else:
                recent_streak.append("D")
    recent_streak = "".join(recent_streak)
    current_streak = len(recent_streak) - len(recent_streak.lstrip("X"))

    best_snapshot_rating = player["rating"]
    if snapshots:
        best_snapshot_rating = max(float(row["rating"]) for row in snapshots)

    longest_streak = 0
    current_streak = 0
    last_result = None
    for match in all_matches:
        if match["white_player_id"] == player["id"]:
            result = "W" if match["result"] == "1-0" else "L" if match["result"] == "0-1" else "D"
        else:
            result = "W" if match["result"] == "0-1" else "L" if match["result"] == "1-0" else "D"

        if result == "D":
            current_streak = 0
        elif result == "W":
            current_streak += 1
            longest_streak = max(longest_streak, current_streak)
        else:
            current_streak = 0
            longest_streak = max(longest_streak, 0)

        last_result = result

    last_played = None
    if all_matches:
        last_played = all_matches[0]["match_date"]

    tournaments = []
    available_categories = []
    profile_seasons = sorted({
        str(row["match_date"])[:4]
        for row in all_matches
        if row["match_date"] and str(row["match_date"])[:4].isdigit()
    }, reverse=True)
    has_tournament_tables = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name IN ('tournaments', 'tournament_participants')"
    ).fetchone()[0] == 2
    if has_tournament_tables:
        tournament_rows = conn.execute(
            """
            SELECT t.id, t.name, t.begin_date, t.status, t.pairing_system,
                   t.tournament_type, t.bye_points, t.absent_points,
                   tp.category, tp.initial_score, tp.acceleration, tp.seed_rating,
                   tp.player_id
            FROM tournament_participants tp
            JOIN tournaments t ON t.id = tp.tournament_id
            WHERE tp.player_id = ?
            ORDER BY t.begin_date DESC, t.id DESC
            """,
            (player["id"],),
        ).fetchall()
        available_categories = sorted({row["category"] for row in tournament_rows if row["category"]})
        if category in available_categories:
            tournament_rows = [row for row in tournament_rows if row["category"] == category]
        if season:
            tournament_rows = [row for row in tournament_rows if str(row["begin_date"] or "").startswith(season)]
        for tournament in tournament_rows:
            tournament_id = tournament["id"]
            participants = conn.execute(
                """
                SELECT tp.player_id AS id, p.display_name AS name, p.rating, tp.category,
                       tp.initial_score, tp.acceleration, tp.seed_rating
                FROM tournament_participants tp
                JOIN players p ON p.id = tp.player_id
                WHERE tp.tournament_id = ?
                """,
                (tournament_id,),
            ).fetchall()
            games = conn.execute(
                """
                SELECT p.white_player_id, p.black_player_id, p.result, p.is_bye,
                       r.round_number
                FROM tournament_pairings p
                JOIN tournament_rounds r ON r.id = p.round_id
                WHERE r.tournament_id = ?
                """,
                (tournament_id,),
            ).fetchall()
            standings = calculate_standings(
                participants,
                games,
                tournament_type=tournament["tournament_type"],
                bye_points=tournament["bye_points"],
                absent_points=tournament["absent_points"],
            )
            player_standing = next(
                standing for standing in standings if standing["id"] == player["id"]
            )
            overview_row = dict(tournament)
            overview_row["score"] = player_standing["score"]
            overview_row["final_position"] = player_standing["rank"]
            tournaments.append(overview_row)

    total_tournaments = len(tournaments)
    tournament_pagination = pagination_details(
        total_tournaments, tournament_page, tournament_page_size
    )
    if total_tournaments > 10:
        tournament_page = tournament_pagination["page"]
        tournament_page_size = tournament_pagination["page_size"]
        tournaments = tournaments[
            (tournament_page - 1) * tournament_page_size:
            tournament_page * tournament_page_size
        ]
    else:
        tournament_pagination = pagination_details(total_tournaments, 1, total_tournaments or 10)

    conn.close()

    #
    opponent_records[player["display_name"]] = {
        "name": player["display_name"],
        "id": player['id'],
        "slug": player["slug"],
        "rating": player["rating"],
        "wins": None,
        "losses": None,
        "draws": None,
        "is_self": True,
    }

    opponent_records = sorted(
        opponent_records.values(),
        key=lambda x: x["rating"],
        reverse=True
    )

    return {
        "player": dict(player),
        "matches": [dict(match) for match in matches],
        "page": page,
        "page_size": page_size,
        "total_matches": total_matches,
        "season": season,
        "category": category or "",
        "available_categories": available_categories,
        "profile_seasons": profile_seasons,
        "tournaments": tournaments,
        "total_tournaments": total_tournaments,
        "tournament_pagination": tournament_pagination,
        "stats": {
            "white": {
                "games": white_stats["games"],
                "wins": white_stats["wins"],
                "losses": white_stats["losses"],
                "draws": white_stats["draws"],
                "win_rate": white_win_rate,
            },
            "black": {
                "games": black_stats["games"],
                "wins": black_stats["wins"],
                "losses": black_stats["losses"],
                "draws": black_stats["draws"],
                "win_rate": black_win_rate,
            },
            "total": {
                **total_results,
                "win_rate": overall_win_rate,
            },
            "yearly": yearly_results,
            "quarterly": quarterly_results,
            "recent_streak": recent_streak,
            "milestones": {
                "best_rating": best_snapshot_rating,
                "streak_count": longest_streak,
                "current_streak": current_streak,
                "last_played": last_played,
            },
        },
        "rating_history": [dict(row) for row in snapshots],
        "rating_chart": build_rating_chart_data([dict(row) for row in snapshots]),
        "opponent_records": [dict(row) for row in opponent_records],
        "badges": badges,
    }



def parse_page_number(value, default=1, minimum=1):
    try:
        page = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, page)


def parse_page_size(value, default=25, maximum=100):
    try:
        page_size = int(value)
    except (TypeError, ValueError):
        return default
    if page_size <= 0:
        return default
    return min(maximum, page_size)


def pagination_details(total_count, page, page_size):
    """Return stable page totals and result bounds for list views."""
    total_count = max(0, int(total_count or 0))
    page = parse_page_number(page)
    page_size = parse_page_size(page_size)
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    page = min(page, total_pages)
    start = (page - 1) * page_size + 1 if total_count else 0
    end = min(page * page_size, total_count)
    return {
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "result_start": start,
        "result_end": end,
    }


def _build_player_name_clause(conn, display_name):
    if not display_name:
        return "", []

    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='players_fts'"
    ).fetchone()
    if table_exists:
        fts_query = f"{display_name.lower()}*"
        rows = conn.execute(
            "SELECT rowid FROM players_fts WHERE players_fts MATCH ?",
            (fts_query,),
        ).fetchall()
        if rows:
            return " AND id IN (SELECT rowid FROM players_fts WHERE players_fts MATCH ?)", [fts_query]

    return " AND LOWER(display_name) LIKE ?", [f"%{display_name.lower()}%"]


def parse_last_active_filter(value):
    if value in (None, "", "all"):
        return None

    try:
        days = int(value)
    except (TypeError, ValueError):
        return None

    return max(1, days) if days > 0 else None


def parse_player_sort(value, default="rating"):
    key = (value or default).strip().lower()
    return key if key in {"name", "rating", "activity"} else default


def parse_player_order(value, default="desc"):
    order = (value or default).strip().lower()
    return order if order in {"asc", "desc"} else default


PLAYER_SORT_FIELDS = {
    "name": "LOWER(display_name)",
    "rating": "COALESCE(rating, 0)",
    "activity": "COALESCE((SELECT MAX(match_date) FROM (SELECT white_player_id AS player_id, match_date FROM matches UNION ALL SELECT black_player_id AS player_id, match_date FROM matches) WHERE player_id = players.id), '1970-01-01')",
}


def count_rankings(filters=None):
    filters = filters or {}
    display_name = (filters.get("display_name") or "").strip()
    glicko_min = parse_rating_filter(filters.get("glicko_min"))
    glicko_max = parse_rating_filter(filters.get("glicko_max"))
    games_played_min = filters.get("games_played_min")
    if games_played_min is not None:
        try:
            games_played_min = int(games_played_min)
        except (TypeError, ValueError):
            games_played_min = None

    conn = get_db()
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT COUNT(*) FROM players WHERE 1=1"
        params = []

        if display_name:
            name_clause, name_params = _build_player_name_clause(conn, display_name)
            query += name_clause
            params.extend(name_params)

        if games_played_min is not None:
            query += " AND COALESCE(games_played, 0) >= ?"
            params.append(games_played_min)

        last_active_days = parse_last_active_filter(filters.get("last_active"))
        if last_active_days is not None:
            query += " AND id IN (SELECT player_id FROM (SELECT white_player_id AS player_id, match_date FROM matches UNION ALL SELECT black_player_id AS player_id, match_date FROM matches) WHERE match_date >= date(?, '-' || ? || ' days') GROUP BY player_id)"
            params.extend([current_date().isoformat(), last_active_days])

        if glicko_min is not None:
            query += " AND rating >= ?"
            params.append(glicko_min)
        if glicko_max is not None:
            query += " AND rating <= ?"
            params.append(glicko_max)

        return conn.execute(query, params).fetchone()[0]
    finally:
        conn.close()


def load_rankings(filters=None):
    filters = filters or {}

    display_name = (filters.get("display_name") or "").strip()
    glicko_min = filters.get("glicko_min")
    glicko_max = filters.get("glicko_max")
    games_played_min = filters.get("games_played_min")
    if games_played_min is not None:
        try:
            games_played_min = int(games_played_min)
        except (TypeError, ValueError):
            games_played_min = None

    conn = get_db()
    conn.row_factory = sqlite3.Row
    try:
        category_config = get_category_config()
        recent_summaries = build_recent_result_summaries(conn)

        query = """
            SELECT *
            FROM players
            WHERE 1=1
        """

        params = []

        if display_name:
            name_clause, name_params = _build_player_name_clause(conn, display_name)
            query += name_clause
            params.extend(name_params)

        if games_played_min is not None:
            query += " AND COALESCE(games_played, 0) >= ?"
            params.append(games_played_min)

        last_active_days = parse_last_active_filter(filters.get("last_active"))
        if last_active_days is not None:
            query += " AND id IN (SELECT player_id FROM (SELECT white_player_id AS player_id, match_date FROM matches UNION ALL SELECT black_player_id AS player_id, match_date FROM matches) WHERE match_date >= date(?, '-' || ? || ' days') GROUP BY player_id)"
            params.extend([current_date().isoformat(), last_active_days])

        glicko_min = parse_rating_filter(glicko_min)
        glicko_max = parse_rating_filter(glicko_max)

        if glicko_min is not None:
            query += " AND rating >= ?"
            params.append(glicko_min)

        if glicko_max is not None:
            query += " AND rating <= ?"
            params.append(glicko_max)

        page = parse_page_number(filters.get("page"), default=1)
        page_size = parse_page_size(filters.get("page_size"), default=25)
        offset = (page - 1) * page_size

        sort_key = parse_player_sort(filters.get("sort"))
        sort_order = parse_player_order(filters.get("order"))
        sort_field = PLAYER_SORT_FIELDS[sort_key]
        if sort_key == "rating":
            query += f" ORDER BY {sort_field} {sort_order.upper()}, LOWER(display_name) ASC, id ASC"
        elif sort_key == "name":
            query += f" ORDER BY {sort_field} {sort_order.upper()}, COALESCE(rating, 0) DESC, id ASC"
        else:
            query += f" ORDER BY {sort_field} {sort_order.upper()}, COALESCE(rating, 0) DESC, id ASC"
        query += " LIMIT ? OFFSET ?"
        params.extend([page_size, offset])

        players = conn.execute(query, params).fetchall()

        rankings = []

        for index, row in enumerate(players, start=offset + 1):
            player_id = row["id"]

            recent_results = recent_summaries.get(player_id, "")

            overall = (
                f"{row['wins']}-"
                f"{row['losses']}-"
                f"{row['draws']}"
            )

            rankings.append(
                {
                    **dict(row),
                    "rank": index,
                    "category": glicko_to_category(
                        row["rating"],
                        k=category_config["glicko_k"],
                        m=category_config["glicko_m"],
                    ),
                    "recent_results": recent_results,
                    "overall_results": overall,
                }
            )

        return rankings
    finally:
        conn.close()
