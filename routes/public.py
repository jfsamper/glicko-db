# routes/public.py

from datetime import datetime
import math

from flask import Blueprint, Response, render_template, request, flash, redirect, url_for

from services.category_service import get_category_config, glicko_to_category
from services.common import (
    TRANSLATIONS,
    get_language,
    get_db,
)

from services.home_stats import build_home_stats
from services.reporting_service import (
    build_date_report,
    export_report_csv,
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


def _report_request():
    period = request.args.get("period", "year")
    start_date, end_date = resolve_report_range(
        period,
        request.args.get("start_date"),
        request.args.get("end_date"),
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


@public_bp.route("/reports")
def reports():
    lang = get_language(request.args.get("lang"))
    try:
        report = _report_request()
    except ValueError as exc:
        return Response(str(exc), status=400)
    return render_template(
        "reports.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        report=report,
        period=request.args.get("period", "year"),
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

    conn = get_db()

    total_count = conn.execute(
        "SELECT COUNT(*) FROM matches"
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
        {order_sql}
        LIMIT ? OFFSET ?
        """,
        (page_size, (page - 1) * page_size),
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
        **pagination_details(total_count, page, page_size),
    )


@public_bp.route("/tournaments")
def tournaments():
    lang = get_language(request.args.get("lang"))
    show_drafts = show_drafts_requested()
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
    total_count = conn.execute(
        f"SELECT COUNT(*) FROM tournaments t WHERE {status_filter}"
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
        WHERE {status_filter}
        ORDER BY {sort_expr} {sort_order.upper()}, t.id DESC
        LIMIT ? OFFSET ?
        """,
        (page_size, (page - 1) * page_size),
    ).fetchall()
    conn.close()
    tournaments = [dict(tournament) for tournament in tournaments]
    return render_template(
        "tournaments.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        tournaments=tournaments,
        show_drafts=show_drafts,
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

