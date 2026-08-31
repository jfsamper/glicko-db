# routes/public.py

from datetime import datetime
import math
import re

from flask import Blueprint, Response, jsonify, render_template, request, flash, redirect, session, url_for

from services.category_service import get_category_config, glicko_to_category
from services.common import (
    TRANSLATIONS,
    get_current_user,
    get_language,
    get_db,
)

from services.home_stats import build_home_stats
from services.reporting_service import (
    build_date_report,
    export_report_csv,
    export_report_pdf,
    list_report_seasons,
    resolve_report_range,
)
from services.player_service import (
    count_rankings,
    load_player,
    load_rankings,
    pagination_details,
    parse_page_number,
    parse_page_size,
    parse_player_order,
    parse_player_sort,
)
from services.tournament_service import get_tournament_standings, TOURNAMENT_STATUSES

public_bp = Blueprint("public", __name__)

MATCH_SORT_FIELDS = {
    "date": "m.match_date",
    "white": "LOWER(p_white.display_name)",
    "black": "LOWER(p_black.display_name)",
    "result": "CASE m.result WHEN '1-0' THEN 0 WHEN '1/2-1/2' THEN 1 WHEN '0-1' THEN 2 ELSE 3 END",
    "round": "m.round_number",
}


def _parse_match_sort(sort_value, default_sort="date"):
    key = (sort_value or default_sort).strip().lower()
    return key if key in MATCH_SORT_FIELDS else default_sort


def _parse_match_order(order_value):
    value = (order_value or "desc").strip().lower()
    return value if value in {"asc", "desc"} else "desc"


def _parse_match_filters(args):
    date_from = (args.get("date_from") or "").strip()
    date_to = (args.get("date_to") or "").strip()
    for date_name in ("date_from", "date_to"):
        date_value = date_from if date_name == "date_from" else date_to
        if date_value:
            try:
                datetime.strptime(date_value, "%Y-%m-%d")
            except ValueError:
                if date_name == "date_from":
                    date_from = ""
                else:
                    date_to = ""

    player_id = (args.get("player_id") or "").strip()
    if not player_id.isdigit():
        player_id = ""
    return date_from, date_to, player_id


def _match_filter_sql(date_from, date_to, player_id):
    clauses = []
    params = []
    if date_from:
        clauses.append("m.match_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("m.match_date <= ?")
        params.append(date_to)
    if player_id:
        clauses.append("(m.white_player_id = ? OR m.black_player_id = ?)")
        params.extend([int(player_id), int(player_id)])
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


TEAM_ROLE_ALIASES = {
    "Presidente": ["cruz, carlos", "carlos cruz"],
    "Secretario": ["gaitan, carlos", "carlos gaitan"],
    "Tesorero": ["rivera, juan", "juan rivera"],
}


def _normalize_team_alias(alias):
    return (alias or "").strip().lower()


def _team_alias_variants(aliases):
    variants = set()
    for alias in aliases:
        normalized = _normalize_team_alias(alias)
        if not normalized:
            continue
        variants.add(normalized)
        variants.add(normalized.replace(", ", " "))
    return sorted(variants)


def get_team_members(conn=None):
    own_conn = conn is None
    if conn is None:
        conn = get_db()

    try:
        role_aliases = {
            role_label: _team_alias_variants(aliases)
            for role_label, aliases in TEAM_ROLE_ALIASES.items()
        }
        all_aliases = []
        for aliases in role_aliases.values():
            all_aliases.extend(aliases)

        unique_aliases = []
        seen_aliases = set()
        for alias in all_aliases:
            if alias and alias not in seen_aliases:
                unique_aliases.append(alias)
                seen_aliases.add(alias)

        if not unique_aliases:
            return []

        placeholders = ", ".join(["?"] * len(unique_aliases))
        rows = conn.execute(
            f"""
            SELECT *
            FROM players
            WHERE LOWER(display_name) IN ({placeholders})
               OR LOWER(REPLACE(display_name, ', ', ' ')) IN ({placeholders})
            """,
            (*unique_aliases, *unique_aliases),
        ).fetchall()

        members = []
        for role_label, aliases in role_aliases.items():
            chosen = None
            for row in rows:
                display_name = _normalize_team_alias(row["display_name"])
                if display_name in aliases or display_name.replace(", ", " ") in aliases:
                    chosen = row
                    break
            if chosen is not None:
                members.append({"role": role_label, "player": dict(chosen)})
        return members
    finally:
        if own_conn:
            conn.close()


def get_public_tournament_status(tournament):
    """Return the stored status shared by public and admin views."""
    status = tournament["status"]
    return status if status in TOURNAMENT_STATUSES else "draft"


def show_drafts_requested():
    return str(request.args.get("show_drafts", "")).strip().lower() in {"1", "true", "yes", "on"}


TOURNAMENT_SORT_FIELDS = {
    "name": "LOWER(t.name)",
    "date": "COALESCE(t.begin_date, t.created_at)",
    "status": "CASE t.status WHEN 'draft' THEN 0 WHEN 'active' THEN 1 WHEN 'canceled' THEN 2 WHEN 'completed' THEN 3 ELSE 4 END",
    "participants": "0",
}


def parse_tournament_sort(sort_value, default_sort="date"):
    key = (sort_value or default_sort).strip().lower()
    return key if key in TOURNAMENT_SORT_FIELDS else default_sort


def parse_tournament_order(order_value, default_order="desc"):
    value = (order_value or default_order).strip().lower()
    return value if value in {"asc", "desc"} else default_order


@public_bp.route("/")
def index():
    lang = get_language(request.args.get("lang"))
    rankings = load_rankings()
    stats = build_home_stats()
    category_config = get_category_config()
    team_members = get_team_members()

    return render_template(
        "index.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        rankings=rankings,
        home_stats=stats,
        team_members=team_members,
        glicko_to_category=lambda rating, decimals=0: glicko_to_category(
            rating,
            decimals,
            k=category_config["glicko_k"],
            m=category_config["glicko_m"],
        ),
    )


@public_bp.route("/preferences", methods=["POST"])
def save_preferences():
    values = request.get_json(silent=True) or {}
    language = values.get("language")
    theme = values.get("theme")
    if language not in TRANSLATIONS or theme not in {"light", "dark"}:
        return jsonify({"error": "invalid preferences"}), 400

    user = get_current_user()
    if user is not None:
        conn = get_db()
        try:
            conn.execute(
                "UPDATE users SET language = ?, theme = ? WHERE id = ?",
                (language, theme, user["id"]),
            )
            conn.commit()
        finally:
            conn.close()
        session["user_language"] = language
        session["user_theme"] = theme

    response = jsonify({"language": language, "theme": theme})
    response.set_cookie("user_language", language, max_age=31536000, samesite="Lax")
    response.set_cookie("user_theme", theme, max_age=31536000, samesite="Lax")
    return response


def _report_request():
    season = request.args.get("season", "").strip()
    period = request.args.get("period", "all_time")
    if season:
        if not (season.isdigit() and len(season) == 4):
            raise ValueError("Invalid report season")
        period = "year"
        start_value = f"{season}-01-01"
        end_value = f"{season}-12-31"
    else:
        start_value = request.args.get("start_date")
        end_value = request.args.get("end_date")
    start_date, end_date = resolve_report_range(
        period,
        start_value,
        end_value,
    )
    conn = get_db()
    try:
        return build_date_report(
            conn,
            start_date=start_date,
            end_date=end_date,
            selected_player_id=request.args.get("player_id"),
        )
    finally:
        conn.close()


def _report_period_label(period, report, translations):
    if period == "all_time":
        return translations.get("period_all_time", translations.get("all_time", "All time"))
    if period == "custom":
        start = report["start_date"] or ""
        end = report["end_date"] or ""
        return f"{start} - {end}".strip(" -")
    return translations.get(f"period_{period}", period)


def _report_filename_part(value):
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
    return value or "report"


@public_bp.route("/reports")
def reports():
    lang = get_language(request.args.get("lang"))
    season = request.args.get("season", "").strip()
    period = "year" if season else request.args.get("period", "all_time")
    page = parse_page_number(request.args.get("page"), default=1)
    page_size = parse_page_size(request.args.get("page_size"), default=25)
    conn = get_db()
    try:
        report_seasons = list_report_seasons(conn)
    finally:
        conn.close()
    try:
        report = _report_request()
    except ValueError as exc:
        return Response(str(exc), status=400)
    total_count = len(report["players"])
    page_details = pagination_details(total_count, page, page_size)
    report["players"] = report["players"][(page_details["page"] - 1) * page_details["page_size"]:page_details["page"] * page_details["page_size"]]
    return render_template(
        "reports.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        report=report,
        period=period,
        season=season,
        report_seasons=report_seasons,
        period_label=season or _report_period_label(period, report, TRANSLATIONS[lang]),
        total_count=total_count,
        **page_details,
    )


@public_bp.route("/reports/export.csv")
def report_export():
    lang = get_language(request.args.get("lang"))
    try:
        report = _report_request()
    except ValueError as exc:
        return Response(str(exc), status=400)
    start = report["start_date"] or "all"
    end = report["end_date"] or "time"
    return Response(
        export_report_csv(report),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=report_{start}_{end}.csv"},
    )


@public_bp.route("/reports/export.pdf")
def report_export_pdf_file():
    lang = get_language(request.args.get("lang"))
    season = request.args.get("season", "").strip()
    period = "year" if season else request.args.get("period", "all_time")
    try:
        report = _report_request()
    except ValueError as exc:
        return Response(str(exc), status=400)
    selected_player = next(
        (row for row in report["players"] if row["player_id"] == report["selected_player_id"]),
        None,
    )
    player_part = _report_filename_part(selected_player["display_name"]) if selected_player else "all-players"
    period_label = season or _report_period_label(period, report, TRANSLATIONS[lang])
    period_part = (
        f"{report['start_date'] or 'start'}_to_{report['end_date'] or 'end'}"
        if period == "custom"
        else _report_filename_part(period_label)
    )
    return Response(
        export_report_pdf(report, TRANSLATIONS[lang], period_label),
        content_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=report_{player_part}_{period_part}.pdf"},
    )


@public_bp.route("/category")
def category_converter():
    lang = get_language(request.args.get("lang"))
    category_config = get_category_config()
    k = category_config["glicko_k"]
    m = category_config["glicko_m"]

    glicko = request.args.get("glicko")
    category = None

    if glicko:
        try:
            category = glicko_to_category(float(glicko), 1, k=k, m=m)
        except ValueError:
            pass

    #
    categories = []

    for r in range(8, -31, -1):
        glicko = round(m * math.exp((r + 29) / k))

        if r >= 0:
            display_category = f"{r + 1} dan"
        else:
            display_category = f"{-r} kyu"

        categories.append({
            "display_category": display_category,
            "category": glicko_to_category(glicko, k=k, m=m),
            "glicko": glicko,
        })

    max_glicko = categories[0]["glicko"]

    return render_template(
        "category.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        glicko=request.args.get("glicko"),
        category=category,
        categories=categories,
        max_glicko=max_glicko,
        k=k,
        m=m,
    )


@public_bp.route("/rankings")
def rankings():
    lang = get_language(request.args.get("lang"))
    page = parse_page_number(request.args.get("page"), default=1)
    page_size = parse_page_size(request.args.get("page_size"), default=50)
    filters = {"page": page, "page_size": page_size, "games_played_min": 1}
    rankings = load_rankings(filters)
    total_count = count_rankings({"games_played_min": 1})
    return render_template(
        "rankings.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        rankings=rankings,
        total_count=total_count,
        **pagination_details(total_count, page, page_size),
    )


@public_bp.route("/players")
def players():
    lang = get_language(request.args.get("lang"))
    category_config = get_category_config()
    page = parse_page_number(request.args.get("page"), default=1)
    page_size = parse_page_size(request.args.get("page_size"), default=25)
    sort_key = parse_player_sort(request.args.get("sort"))
    sort_order = parse_player_order(request.args.get("order"))
    filters = {
        "display_name": request.args.get("display_name", "").strip(),
        "glicko_min": request.args.get("glicko_min", "").strip(),
        "glicko_max": request.args.get("glicko_max", "").strip(),
        "last_active": request.args.get("last_active", "").strip(),
        "sort": sort_key,
        "order": sort_order,
        "page": page,
        "page_size": page_size,
    }
    rankings = load_rankings(filters)
    total_count = count_rankings({
        "display_name": filters["display_name"],
        "glicko_min": filters["glicko_min"],
        "glicko_max": filters["glicko_max"],
        "last_active": filters["last_active"],
    })
    return render_template(
        "players.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        rankings=rankings,
        total_count=total_count,
        sort=sort_key,
        order=sort_order,
        category_config=category_config,
        **pagination_details(total_count, page, page_size),
    )


@public_bp.route("/player/view")
def player_profile():

    lang = get_language(request.args.get("lang"))

    player_id = request.args.get("id")

    if not player_id:
        flash(TRANSLATIONS[lang]["error"])
        return redirect(
            url_for("players", lang=lang)
        )

    page = parse_page_number(request.args.get("page"), default=1)
    page_size = parse_page_size(request.args.get("page_size"), default=25)
    season = request.args.get("season", "")
    tournament_page = parse_page_number(request.args.get("tournament_page"), default=1)

    data = load_player(
        player_id, page=page, page_size=page_size, season=season,
        tournament_page=tournament_page,
    )
    if not data:
        flash(TRANSLATIONS[lang]["error"])
        return redirect(
            url_for("players", lang=lang)
        )
    category_config = get_category_config()
    category = glicko_to_category(
        data["player"]["rating"],
        1,
        k=category_config["glicko_k"],
        m=category_config["glicko_m"],
    )
    return render_template(
        "player.html",
        datetime=datetime,
        lang=lang,
        translations=TRANSLATIONS[lang],
        player=data["player"],
        category=category,
        matches=data["matches"],
        total_matches=data.get("total_matches", 0),
        **pagination_details(data.get("total_matches", 0), data.get("page", 1), data.get("page_size", 25)),
        stats=data["stats"],
        rating_history=data["rating_history"],
        rating_chart=data["rating_chart"],
        opponent_records=data["opponent_records"],
        badges=data["badges"],
        season=data.get("season", ""),
        profile_seasons=data.get("profile_seasons", []),
        tournaments=data.get("tournaments", []),
        total_tournaments=data.get("total_tournaments", 0),
        tournament_pagination=data.get("tournament_pagination", {}),
        glicko_to_category=lambda rating, decimals=0: glicko_to_category(
            rating,
            decimals,
            k=category_config["glicko_k"],
            m=category_config["glicko_m"],
        ),
    )


@public_bp.route("/matches")
def matches():
    lang = get_language(request.args.get("lang"))
    page = parse_page_number(request.args.get("page"), default=1)
    page_size = parse_page_size(request.args.get("page_size"), default=25)
    sort_key = _parse_match_sort(request.args.get("sort"))
    sort_order = _parse_match_order(request.args.get("order"))
    date_from, date_to, player_id = _parse_match_filters(request.args)
    filter_sql, filter_params = _match_filter_sql(date_from, date_to, player_id)

    conn = get_db()

    total_count = conn.execute(
        f"SELECT COUNT(*) FROM matches m {filter_sql}",
        filter_params,
    ).fetchone()[0]

    order_clause = MATCH_SORT_FIELDS[sort_key]
    order_sql = f"ORDER BY {order_clause} {sort_order.upper()}, m.match_date DESC, m.round_number DESC, m.id DESC"

    matches = conn.execute(
        f"""
        SELECT
            m.id,
            m.match_date,
            m.notes,
            m.round_number,
            m.event,
            p_white.display_name AS white_name,
            p_black.display_name AS black_name,
            p_white.id AS white_id,
            p_black.id AS black_id,
            m.result
        FROM matches m
        JOIN players p_white
            ON p_white.id = m.white_player_id
        JOIN players p_black
            ON p_black.id = m.black_player_id
        {filter_sql}
        {order_sql}
        LIMIT ? OFFSET ?
        """,
        (*filter_params, page_size, (page - 1) * page_size),
    ).fetchall()

    match_players = conn.execute(
        "SELECT id, display_name FROM players ORDER BY display_name"
    ).fetchall()

    conn.close()

    return render_template(
        "matches.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        matches=[dict(m) for m in matches],
        total_count=total_count,
        sort=sort_key,
        order=sort_order,
        date_from=date_from,
        date_to=date_to,
        player_id=player_id,
        match_players=match_players,
        **pagination_details(total_count, page, page_size),
    )


@public_bp.route("/tournaments")
def tournaments():
    lang = get_language(request.args.get("lang"))
    show_drafts = show_drafts_requested()
    search_term = request.args.get("search", "").strip()
    page = parse_page_number(request.args.get("page"), default=1)
    page_size = parse_page_size(request.args.get("page_size"), default=25)
    sort_key = parse_tournament_sort(request.args.get("sort"))
    sort_order = parse_tournament_order(request.args.get("order"))
    conn = get_db()
    status_filter = "t.status IN ('active', 'canceled', 'completed')"
    if show_drafts:
        status_filter = "t.status IN ('draft', 'active', 'canceled', 'completed')"

    participant_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('tournament_participants', 'tournament_pending_players')"
        ).fetchall()
    }
    has_participant_tables = {"tournament_participants", "tournament_pending_players"}.issubset(participant_tables)
    participant_count_expr = "0" if not has_participant_tables else "(SELECT COUNT(*) FROM tournament_participants WHERE tournament_id = t.id) + (SELECT COUNT(*) FROM tournament_pending_players WHERE tournament_id = t.id)"
    sort_expr = TOURNAMENT_SORT_FIELDS[sort_key] if sort_key != "participants" else participant_count_expr
    tournament_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(tournaments)").fetchall()
    }
    tournament_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    match_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(matches)").fetchall()
    } if "matches" in tournament_tables else set()
    has_linked_match_tables = {
        "matches", "tournament_pairings", "tournament_rounds"
    }.issubset(tournament_tables)
    search_conditions = []
    search_params = []
    if search_term:
        search_pattern = f"%{search_term}%"
        search_conditions.append("LOWER(t.name) LIKE LOWER(?)")
        search_params.append(search_pattern)
        if "description" in tournament_columns:
            search_conditions.append("LOWER(COALESCE(t.description, '')) LIKE LOWER(?)")
            search_params.append(search_pattern)
        match_conditions = []
        for column in ("event", "notes", "result", "match_date"):
            if column in match_columns:
                match_conditions.append(f"LOWER(COALESCE(m.{column}, '')) LIKE LOWER(?)")
                search_params.append(search_pattern)
        if has_linked_match_tables and {"tournament_pairing_id"}.issubset(match_columns) and match_conditions:
            search_conditions.append(
                """
                EXISTS (
                    SELECT 1
                    FROM matches m
                    JOIN tournament_pairings tp ON tp.id = m.tournament_pairing_id
                    JOIN tournament_rounds tr ON tr.id = tp.round_id
                    WHERE tr.tournament_id = t.id
                      AND ({})
                )
                """.format(" OR ".join(match_conditions))
            )
        if has_linked_match_tables and {"tournament_pairing_id"}.issubset(match_columns) and "display_name" in {
            row[1] for row in conn.execute("PRAGMA table_info(players)").fetchall()
        }:
            search_conditions.append(
                """
                EXISTS (
                    SELECT 1
                    FROM matches m
                    JOIN tournament_pairings tp ON tp.id = m.tournament_pairing_id
                    JOIN tournament_rounds tr ON tr.id = tp.round_id
                    JOIN players p_white ON p_white.id = m.white_player_id
                    JOIN players p_black ON p_black.id = m.black_player_id
                    WHERE tr.tournament_id = t.id
                      AND (LOWER(p_white.display_name) LIKE LOWER(?)
                           OR LOWER(p_black.display_name) LIKE LOWER(?))
                )
                """
            )
            search_params.extend((search_pattern, search_pattern))
    where_clause = status_filter
    if search_conditions:
        where_clause = f"{where_clause} AND ({' OR '.join(search_conditions)})"
    total_count = conn.execute(
        f"SELECT COUNT(*) FROM tournaments t WHERE {where_clause}",
        search_params,
    ).fetchone()[0]
    page_details = pagination_details(total_count, page, page_size)
    page = page_details["page"]
    page_size = page_details["page_size"]

    tournaments = conn.execute(
        f"""
             SELECT t.*,
                 CASE
                     WHEN t.status = 'draft' THEN 'draft'
                     WHEN t.status = 'active' THEN 'active'
                     WHEN t.status = 'canceled' THEN 'canceled'
                     WHEN t.status = 'completed' THEN 'completed'
                     ELSE 'draft'
                 END AS public_status,
                 {participant_count_expr} AS participant_count
        FROM tournaments t
        WHERE {where_clause}
        ORDER BY {sort_expr} {sort_order.upper()}, t.id DESC
        LIMIT ? OFFSET ?
        """,
        (*search_params, page_size, (page - 1) * page_size),
    ).fetchall()
    conn.close()
    tournaments = [dict(tournament) for tournament in tournaments]
    return render_template(
        "tournaments.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        tournaments=tournaments,
        show_drafts=show_drafts,
        search_term=search_term,
        sort=sort_key,
        order=sort_order,
        total_count=total_count,
        **page_details,
    )


@public_bp.route("/tournaments/<int:tournament_id>")
def tournament_page(tournament_id):
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    tournament = conn.execute(
        """
        SELECT t.*,
               CASE
                   WHEN t.status = 'draft' THEN 'draft'
                   WHEN t.status = 'active' THEN 'active'
                   WHEN t.status = 'canceled' THEN 'canceled'
                   WHEN t.status = 'completed' THEN 'completed'
                   ELSE 'draft'
               END AS public_status
        FROM tournaments t
        WHERE t.id = ? AND t.status IN ('draft', 'active', 'canceled', 'completed')
        """,
        (tournament_id,),
    ).fetchone()
    if tournament is None:
        conn.close()
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("tournaments", lang=lang))

    rounds = conn.execute(
        """
        SELECT id, round_number, status
        FROM tournament_rounds
        WHERE tournament_id = ?
        ORDER BY round_number DESC
        """,
        (tournament_id,),
    ).fetchall()
    selected_round_id = request.args.get("round_id", type=int)
    if selected_round_id is None and rounds:
        selected_round_id = rounds[0]["id"]
    pairings = conn.execute(
        """
        SELECT p.board_number, p.is_bye, p.result,
               p.white_player_id, p.black_player_id,
               COALESCE(white.display_name, p.white_player_name) AS white_name,
               COALESCE(black.display_name, p.black_player_name) AS black_name
        FROM tournament_pairings p
        LEFT JOIN players white ON white.id = p.white_player_id
        LEFT JOIN players black ON black.id = p.black_player_id
        WHERE p.round_id = ?
        ORDER BY p.board_number
        """,
        (selected_round_id,),
    ).fetchall() if selected_round_id else []
    standings = get_tournament_standings(conn, tournament_id)
    conn.close()
    return render_template(
        "tournament.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        tournament=tournament,
        rounds=rounds,
        selected_round_id=selected_round_id,
        pairings=pairings,
        standings=standings,
    )


def register_public_routes(app):
    if "public" not in app.blueprints:
        app.register_blueprint(public_bp)

