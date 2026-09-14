# routes/admin.py
import csv
import json
import logging
import math
import os
import sqlite3
import time
from pathlib import Path

from werkzeug.utils import secure_filename
from datetime import datetime

from flask import (
    Blueprint,
    abort,
    jsonify,
    Response,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from routes.public import (
    _match_filter_sql,
    _parse_match_filters,
    glicko_to_category,
)
from routes.sort_helpers import (
    MATCH_SORT_FIELDS,
    TOURNAMENT_SORT_FIELDS,
    _parse_match_order,
    _parse_match_sort,
    parse_tournament_order,
    parse_tournament_sort,
)
from services.auth_service import (
    ALLOWED_ROLES,
    authenticate_user,
    bootstrap_default_admin_account,
    create_password_reset_token,
    create_user_account,
    get_current_user,
    migrate_auth_schema,
    reset_password_with_token,
    send_password_reset_email,
    admin_required,
    validate_email_address,
    user_has_permission,
    validate_theme,
)
from services.audit_service import log_admin_action
from services.db import get_db
from services.stats_service import refresh_stats
from services.timezone_service import (
    current_datetime,
    current_timestamp,
    format_timezone_label,
    get_timezone_choices,
    validate_timezone,
)
from services.i18n import TRANSLATIONS, get_language
from services.settings_service import (
    DEFAULT_APPLICATION_SETTINGS,
    get_application_settings,
    update_application_settings,
)
from config import (
    ADMIN_PASSWORD,
    BASE_DIR,
    DB_PATH,
    DEFAULT_RATING,
    LOGIN_WINDOW_SECONDS,
    MAX_LOGIN_ATTEMPTS,
    LANGUAGE_CHOICES,
    DEFAULT_RD,
    DEFAULT_VOLATILITY,
    GLICKO_K,
    GLICKO_M,
    TAU,
    THEME_CHOICES,
    TIMEZONE_CHOICES,
    RECAPTCHA_SITE_KEY,
)
from services.helpers import normalize_key, normalize_round_note, normalize_round_note_for_storage, parse_date_value
from services.import_service import build_import_preview, import_workbook_data
from services.player_service import (
    count_rankings,
    load_rankings,
    pagination_details,
    parse_page_number,
    parse_page_size,
    parse_player_order,
    parse_player_sort,
)
from services.rating_service import (
    get_dirty_date,
    get_rating_config,
    mark_dirty,
    recompute_ratings,
    update_from_latest_snapshot,
    update_rating_config,
)
from services.category_service import get_category_config, update_category_config
from services.recaptcha import verify_recaptcha
from services.sgf_service import (
    backup_sgf_files,
    clear_missing_sgf_links,
    delete_sgf_file,
    ensure_sgf_schema,
    get_sgf_path,
    match_sgf_metadata,
    save_sgf_upload,
    update_sgf_metadata,
)
from services.pairing_service import (
    ACCELERATION_SCHEMES,
    DEFAULT_ACCELERATION_CATEGORIES,
    DEFAULT_ACCELERATION_FLOORS,
    DEFAULT_CATEGORY_ROUNDS,
    DEFAULT_ACCELERATION_SCHEME,
    acceleration_category_settings,
    default_acceleration_rounds,
    parse_rank_category,
    serialize_acceleration_categories,
    validate_acceleration_categories,
    validate_acceleration_scheme,
)
from services.tournament_service import (
    SUPPORTED_SYSTEMS,
    _materialize_pending_players,
    _player_lookup,
    _recalculate_mcmahon_seeds,
    _suggest_player_name,
    delete_tournament,
    list_tournament_participants,
    normalize_tournament_rounds,
    get_tournament_standings,
    TOURNAMENT_STATUSES,
    update_tournament_handicaps,
    normalize_tournament_system,
)
from services.tournament_gotha import create_tournament_from_gotha, export_tournament_results
from services.tournament_pairing import (
    add_participant,
    generate_next_round,
    manual_pair,
    pair_selected_players,
    remove_participant,
    unpair,
    unpair_all,
    update_pairing,
    update_pairing_handicap,
)
from services.tournament_matches import (
    process_tournament_round_matches,
    save_tournament_matches,
    set_pairing_result,
    sync_match_pairing,
    sync_tournament_matches,
)
from services import backup_service
from routes.admin_backups import (
    admin_backups,
    admin_create_backup,
    admin_delete_backup,
    admin_restore_backup,
    register_backup_routes,
)
from routes.admin_import import (
    admin_import,
    import_matches,
    register_import_routes,
)
from routes.admin_matches import (
    admin_add_match,
    admin_delete_match,
    admin_edit_match,
    admin_matches,
    register_match_routes,
)
from routes.admin_sgf import (
    admin_link_sgf,
    admin_unlink_sgf,
    register_sgf_routes,
)
from routes.admin_players import (
    admin_categories,
    admin_delete_player,
    admin_edit_player,
    admin_players,
    admin_ratings,
    register_player_routes,
)
from routes import admin_users as admin_user_routes
from routes.admin_users import register_user_routes
from routes.admin_tournaments import (
    admin_add_tournament_participant,
    admin_delete_pending_player,
    admin_delete_tournament,
    admin_export_tournament_results,
    admin_resolve_pending_player,
    admin_remove_tournament_participant,
    admin_tournament_players,
    admin_tournament,
    admin_create_tournament_player,
    admin_manual_pair,
    admin_pair_selected_players,
    admin_unpair,
    admin_unpair_all,
    admin_update_pairing_handicap,
    admin_update_tournament_settings,
    admin_generate_tournament_round,
    admin_process_tournament_round,
    admin_save_tournament,
    admin_set_tournament_result,
    admin_tournaments,
    register_tournament_detail_routes,
    register_tournament_routes,
)


logger = logging.getLogger(__name__)
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
LOGIN_ATTEMPTS = {}
DEFER_IMPORT_REPLAY_ENV = "DEFER_RATING_REPLAY_ON_IMPORT"
def acceleration_scheme_choice(scheme):
    scheme_text = scheme or DEFAULT_ACCELERATION_SCHEME
    for choice, option in ACCELERATION_SCHEMES.items():
        if option["scheme"] == scheme_text:
            return choice
    return "category_limits" if str(scheme_text).startswith("categories:") else "go_three_band"


def acceleration_scheme_from_form(form):
    choice = form.get("acceleration_scheme_choice") or "go_three_band"
    if choice in ACCELERATION_SCHEMES:
        return ACCELERATION_SCHEMES[choice]["scheme"]
    if choice == "category_limits":
        category_count, normalized_floors = validate_acceleration_categories(
            form.get("number_of_categories"),
            form.getlist("category_floor"),
        )
        return serialize_acceleration_categories(category_count, normalized_floors)
    return validate_acceleration_scheme(choice)


def parse_rank_setting(value, default):
    text = str(value or "").strip()
    if not text:
        return int(default)
    if any(label in text.lower() for label in ("dan", "kyu")):
        return parse_rank_category(text)
    if text.upper().endswith("D"):
        return int(text[:-1]) - 1
    if text.upper().endswith("K"):
        return -int(text[:-1])
    return int(text)


def validate_mcmahon_settings(mm_bar, mm_floor, mm_zero):
    bar_value = parse_rank_setting(mm_bar, 8)
    floor_value = parse_rank_setting(mm_floor, -30)
    zero_value = int(mm_zero if str(mm_zero or "").strip() else 0)
    if bar_value <= floor_value or zero_value < 0:
        raise ValueError("Invalid McMahon settings")
    return bar_value, floor_value, zero_value


def ensure_backup_dir():
    backup_service.ensure_backup_dir(BACKUP_DIR)


def run_admin_db_action(action, lang, success_message=None):
    """Run one admin DB action with shared connection and validation handling."""
    conn = get_db()
    try:
        result = action(conn)
    except ValueError as exc:
        conn.rollback()
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
        return None
    finally:
        conn.close()
    if success_message is not None:
        flash(success_message)
    return result


def record_failed_login_attempt(ip_address):
    if not ip_address:
        return False

    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS login_attempts (
            ip_address TEXT NOT NULL,
            attempted_at REAL NOT NULL
        )
        """
    )

    runtime_settings = get_application_settings(
        conn=conn,
        fallback_settings={
            "max_login_attempts": MAX_LOGIN_ATTEMPTS,
            "login_window_seconds": LOGIN_WINDOW_SECONDS,
        }
    )
    now = time.time()
    cutoff = now - runtime_settings["login_window_seconds"]
    conn.execute(
        "DELETE FROM login_attempts WHERE attempted_at < ?",
        (cutoff,),
    )
    conn.execute(
        "INSERT INTO login_attempts (ip_address, attempted_at) VALUES (?, ?)",
        (ip_address, now),
    )
    count = conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE ip_address = ?",
        (ip_address,),
    ).fetchone()[0]
    conn.commit()
    conn.close()
    return count >= runtime_settings["max_login_attempts"]


def clear_login_attempts(ip_address):
    if not ip_address:
        return

    conn = get_db()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS login_attempts ("
        "ip_address TEXT NOT NULL, attempted_at REAL NOT NULL)"
    )
    conn.execute(
        "DELETE FROM login_attempts WHERE ip_address = ?",
        (ip_address,),
    )
    conn.commit()
    conn.close()


def validate_match_form_data(conn, match_date, white_player_id, black_player_id, result, lang):
    valid_results = {"1-0", "0-1", "1/2-1/2"}

    if not match_date or not match_date.strip():
        return False, TRANSLATIONS[lang]["error"]

    try:
        datetime.strptime(match_date.strip(), "%Y-%m-%d")
    except ValueError:
        return False, f"{TRANSLATIONS[lang]['error']}: {TRANSLATIONS[lang]['invalid_date_format']}"

    try:
        white_player_id = int(white_player_id)
        black_player_id = int(black_player_id)
    except (TypeError, ValueError):
        return False, TRANSLATIONS[lang]["error"]

    if white_player_id <= 0 or black_player_id <= 0:
        return False, TRANSLATIONS[lang]["error"]

    if white_player_id == black_player_id:
        return False, TRANSLATIONS[lang]["same_player_error"]

    if result not in valid_results:
        return False, TRANSLATIONS[lang]["error"]

    white_player_exists = conn.execute(
        "SELECT 1 FROM players WHERE id = ?",
        (white_player_id,),
    ).fetchone() is not None
    black_player_exists = conn.execute(
        "SELECT 1 FROM players WHERE id = ?",
        (black_player_id,),
    ).fetchone() is not None

    if not white_player_exists or not black_player_exists:
        return False, TRANSLATIONS[lang]["error"]

    return True, None


def parse_handicap_stones(raw_value):
    """Parses the handicap-stones form field. Blank/missing means 0 (no
    handicap), matching the schema default so existing matches and forms
    that don't set this field at all behave exactly as before. Raises
    ValueError for anything out of the conventional 0-9 stone range.
    """
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return 0
    try:
        value = int(raw_value)
    except ValueError:
        raise ValueError("handicap_stones must be an integer")
    if not (0 <= value <= 9):
        raise ValueError("handicap_stones must be between 0 and 9")
    return value


def defer_import_replay_enabled():
    value = os.getenv(DEFER_IMPORT_REPLAY_ENV, "")
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def run_post_import_replay():
    if defer_import_replay_enabled():
        logger.info(
            "Skipping immediate rating replay after import because %s is enabled.",
            DEFER_IMPORT_REPLAY_ENV,
        )
        refresh_stats()
        return False

    update_from_latest_snapshot()
    refresh_stats()
    return True

def is_async_request():
    """Check if the request is an AJAX XMLHttpRequest."""
    return request.headers.get("X-Requested-With", "").lower() == "xmlhttprequest"

def redirect_or_json(url):
    """Return JSON redirect response for AJAX, or regular redirect otherwise."""
    if is_async_request():
        return jsonify({"ok": True, "redirect_url": url})
    return redirect(url)


def require_permission(permission_name):
    if not user_has_permission(permission_name):
        from flask import abort
        if session.get("user_id") is not None:
            abort(403)
        return redirect(url_for("admin_login", lang=get_language(request.args.get("lang"))))
    return None


def load_players_for_user_link():
    conn = get_db()
    try:
        try:
            return conn.execute(
                "SELECT id, display_name FROM players ORDER BY display_name"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()

def get_backup_path(filename):
    return backup_service.get_backup_path(filename, BACKUP_DIR)


def is_valid_sqlite_backup(path):
    return backup_service.is_valid_sqlite_backup(path)


def get_latest_valid_backup_path():
    return backup_service.get_latest_valid_backup_path(DB_PATH, BACKUP_DIR, BASE_DIR)


def rebuild_players_fts_artifacts(conn):
    backup_service.rebuild_players_fts_artifacts(conn)


def ensure_players_fts_artifacts(conn):
    backup_service.ensure_players_fts_artifacts(conn)


def ensure_rating_state_table(conn):
    backup_service.ensure_rating_state_table(conn)


def restore_db_from_backup(path):
    return backup_service.restore_db_from_backup(path, DB_PATH)


admin_bp = Blueprint("admin", __name__)
register_backup_routes(admin_bp)
register_import_routes(admin_bp)
register_match_routes(admin_bp)
register_sgf_routes(admin_bp)
register_player_routes(admin_bp)
register_user_routes(admin_bp)
register_tournament_routes(admin_bp)
register_tournament_detail_routes(admin_bp)

ADMIN_ROUTE_PERMISSIONS = {
    "admin.admin_backups": "admin",
    "admin.admin_create_backup": "admin",
    "admin.admin_create_user": "admin",
    "admin.admin_edit_user": "admin",
    "admin.admin_delete_user": "admin",
    "admin.admin_audit_review": "admin",
    "admin.admin_settings": "admin",
    "admin.admin_users": "admin",
    "admin.admin_result_submissions": "operator",
    "admin.admin_link_sgf": "operator",
    "admin.admin_link_sgf_alias": "operator",
    "admin.admin_unlink_sgf": "operator",
    "admin.admin_unlink_sgf_alias": "operator",
    "admin.admin_delete_sgf": "admin",
    "admin.admin_approve_result_submission": "operator",
    "admin.admin_reject_result_submission": "operator",
    "admin.admin_profile": "results_submitter",
    "admin.admin_report_results": "results_submitter",
    "admin.admin_logout": "results_submitter",
    "admin.admin_import": "operator",
    "admin.admin_matches": "operator",
    "admin.admin_tournaments": "operator",
    "admin.admin_tournament": "operator",
    "admin.admin_tournament_settings": "operator",
    "admin.admin_players": "data_admin",
    "admin.admin_edit_player": "data_admin",
    "admin.admin_delete_player": "data_admin",
    "admin.admin_ratings": "data_admin",
    "admin.admin_categories": "data_admin",
}

ADMIN_MENU_SECTIONS = (
    (
        "admin_tournament_operations_heading",
        (
            ("admin_import", "admin_import_title", "admin_import_desc", "operator"),
            ("admin_matches", "admin_matches_title", "admin_matches_desc", "operator"),
            ("sgf_library", "sgf_library_title", "sgf_library_desc", "operator"),
            ("admin_tournaments", "tournaments_title", "tournaments_desc", "operator"),
        ),
    ),
    (
        "admin_data_management_heading",
        (
            ("admin_players", "admin_players_title", "admin_players_desc", "data_admin"),
            ("admin_ratings", "admin_ratings_title", "admin_ratings_desc", "data_admin"),
            ("admin_categories", "admin_categories_title", "admin_categories_desc", "data_admin"),
        ),
    ),
    (
        "admin_management_heading",
        (
            ("admin_backups", "admin_backups_title", "admin_backups_desc", "admin"),
            ("admin_users", "admin_users_title", "admin_users_desc", "admin"),
            ("admin_result_submissions", "result_submissions_title", "result_submissions_desc", "operator"),
            ("admin_audit_review", "audit_review_heading", "audit_review_desc", "admin"),
            ("admin_settings", "admin_settings_title", "admin_settings_desc", "admin"),
        ),
    ),
)


def get_required_permission_for_route(endpoint_name):
    return ADMIN_ROUTE_PERMISSIONS.get(endpoint_name, "operator")


@admin_bp.before_request
def admin_auth_check():
    if request.path in {"/admin/login", "/admin/register", "/admin/forgot-password", "/admin/forgot_password"} or (
        request.endpoint
        and request.endpoint.endswith(("admin_login", "admin_forgot_password", "admin_reset_password"))
    ):
        return None
    if request.path == "/import":
        return None

    user = get_current_user()
    if user is not None:
        session["user_role"] = user.get("role")
        if user.get("language"):
            session["user_language"] = user["language"]
        if user.get("theme"):
            session["user_theme"] = user["theme"]
        if request.path == "/admin" and user.get("role") == "member":
            return redirect(url_for("admin_report_results", lang=get_language(request.args.get("lang"))))
        required_permission = get_required_permission_for_route(request.endpoint)
        if not user_has_permission(required_permission):
            from flask import abort
            abort(403)
        return None

    if session.get("user_id") is not None:
        session.clear()

    lang = get_language(request.args.get("lang"))
    flash(TRANSLATIONS[lang]["invalid_password"])
    return redirect(url_for("admin.admin_login", lang=lang))

@admin_bp.route("/admin")
def admin():
    lang = get_language(request.args.get("lang"))
    translations = TRANSLATIONS[lang]
    menu_sections = []
    for heading_key, items in ADMIN_MENU_SECTIONS:
        visible_items = [
            {
                "endpoint": endpoint,
                "title": translations_key,
                "description": description_key,
            }
            for endpoint, translations_key, description_key, permission in items
            if user_has_permission(permission)
        ]
        if visible_items:
            menu_sections.append({"heading": heading_key, "items": visible_items})

    return render_template(
        "admin/index.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        menu_sections=menu_sections,
    )

admin_login = admin_user_routes.admin_login
admin_register = admin_user_routes.admin_register
admin_report_results = admin_user_routes.admin_report_results
admin_result_submissions = admin_user_routes.admin_result_submissions
admin_result_submission_sgf = admin_user_routes.admin_result_submission_sgf
admin_approve_result_submission = admin_user_routes.admin_approve_result_submission
admin_reject_result_submission = admin_user_routes.admin_reject_result_submission
admin_settings = admin_user_routes.admin_settings
admin_profile = admin_user_routes.admin_profile
admin_forgot_password = admin_user_routes.admin_forgot_password
admin_reset_password = admin_user_routes.admin_reset_password
admin_logout = admin_user_routes.admin_logout

def _audit_details_summary(details):
    if not details:
        return "—"
    try:
        payload = json.loads(details)
    except (TypeError, ValueError):
        text = str(details)
        return text if len(text) <= 120 else f"{text[:117]}..."

    if isinstance(payload, dict):
        items = []
        for key, value in list(payload.items())[:3]:
            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            items.append(f"{key}: {value}")
        summary = ", ".join(items)
        return summary if summary and len(summary) <= 120 else f"{summary[:117]}..."
    if isinstance(payload, (list, tuple)):
        summary = ", ".join(str(value) for value in payload[:3])
        return summary if summary and len(summary) <= 120 else f"{summary[:117]}..."

    text = str(payload)
    return text if len(text) <= 120 else f"{text[:117]}..."


admin_audit_review = admin_user_routes.admin_audit_review
admin_users = admin_user_routes.admin_users
admin_create_user = admin_user_routes.admin_create_user
admin_edit_user = admin_user_routes.admin_edit_user
admin_delete_user = admin_user_routes.admin_delete_user


def register_admin_routes(app):
    if "admin" not in app.blueprints:
        app.register_blueprint(admin_bp)
