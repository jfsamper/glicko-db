# routes/admin.py
import csv
import json
import logging
import math
import os
import sqlite3
import time
from pathlib import Path
import re
import shutil

from werkzeug.utils import secure_filename
from datetime import datetime

from flask import (
    Blueprint,
    jsonify,
    Response,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from routes.public import (
    _match_filter_sql,
    _parse_match_filters,
    glicko_to_category,
    parse_tournament_order,
    parse_tournament_sort,
)
from services.common import (
    TRANSLATIONS,
    authenticate_user,
    bootstrap_default_admin_account,
    create_password_reset_token,
    create_user_account,
    get_current_user,
    current_datetime,
    current_timestamp,
    format_timezone_label,
    get_timezone_choices,
    get_db,
    get_language,
    log_admin_action,
    migrate_auth_schema,
    reset_password_with_token,
    refresh_stats,
    send_password_reset_email,
    admin_required,
    validate_email_address,
    user_has_permission,
    validate_timezone,
    validate_theme,
)
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
from services.common import ALLOWED_ROLES
from services.helpers import normalize_key, normalize_round_note, normalize_round_note_for_storage, parse_date_value
from services.import_service import build_import_preview, import_gotha_xml, import_workbook_data
from services.player_service import (
    count_rankings,
    load_rankings,
    pagination_details,
    parse_page_number,
    parse_page_size,
    parse_player_order,
    parse_player_sort,
)
from services.rating_service import get_dirty_date, get_rating_config, mark_dirty, recompute_ratings, update_from_latest_snapshot, update_rating_config
from services.category_service import get_category_config, update_category_config
from services.recaptcha import verify_recaptcha
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
    add_participant,
    create_tournament_from_gotha,
    delete_tournament,
    export_tournament_results,
    generate_next_round,
    list_tournament_participants,
    manual_pair,
    normalize_tournament_rounds,
    process_tournament_round_matches,
    remove_participant,
    get_tournament_standings,
    TOURNAMENT_STATUSES,
    save_tournament_matches,
    unpair,
    set_pairing_result,
    set_round_player_status,
    pair_selected_players,
    sync_match_pairing,
    sync_tournament_matches,
    update_pairing,
    update_tournament_handicaps,
    update_pairing_handicap,
    normalize_tournament_system,
)

BACKUP_DIR = os.path.join(
    BASE_DIR,
    "backups"
)
BACKUP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.db$")
LOGIN_ATTEMPTS = {}
DEFER_IMPORT_REPLAY_ENV = "DEFER_RATING_REPLAY_ON_IMPORT"
MATCH_SORT_FIELDS = {
    "date": "m.match_date",
    "white": "LOWER(p_white.display_name)",
    "black": "LOWER(p_black.display_name)",
    "result": "CASE m.result WHEN '1-0' THEN 0 WHEN '1/2-1/2' THEN 1 WHEN '0-1' THEN 2 ELSE 3 END",
    "round": "m.round_number",
}
TOURNAMENT_SORT_FIELDS = {
    "name": "LOWER(name)",
    "date": "COALESCE(begin_date, created_at)",
    "status": "CASE status WHEN 'draft' THEN 0 WHEN 'active' THEN 1 WHEN 'canceled' THEN 2 WHEN 'completed' THEN 3 ELSE 4 END",
    "participants": "0",
}


logger = logging.getLogger(__name__)


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


def _parse_match_sort(sort_value, default_sort="date"):
    key = (sort_value or default_sort).strip().lower()
    return key if key in MATCH_SORT_FIELDS else default_sort


def _parse_match_order(order_value):
    value = (order_value or "desc").strip().lower()
    return value if value in {"asc", "desc"} else "desc"


def ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


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
    """Return a safe, server-generated backup path or ``None``."""
    if not isinstance(filename, str):
        logger.warning("Rejected backup path with non-string filename: %r", filename)
        return None

    if not BACKUP_NAME_PATTERN.fullmatch(filename):
        logger.warning("Rejected backup path with invalid filename pattern: %r", filename)
        return None

    backup_root = Path(BACKUP_DIR).resolve()
    path = (backup_root / filename).resolve()

    try:
        path.relative_to(backup_root)
    except ValueError:
        logger.warning("Rejected backup path outside backup directory: %r", filename)
        return None

    return path


def is_valid_sqlite_backup(path):
    """Check that a backup file is a healthy SQLite database with real schema content."""
    if path is None or not Path(path).is_file():
        return False

    try:
        with sqlite3.connect(path) as conn:
            table_count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
            ).fetchone()[0]
            if table_count <= 0:
                return False

            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            return integrity is not None and integrity[0] == "ok"
    except sqlite3.DatabaseError:
        return False


def get_latest_valid_backup_path():
    """Return the newest valid backup from managed backups and the data fallback file."""
    candidates = []
    active_db_path = Path(DB_PATH).resolve()

    backup_dir = Path(BACKUP_DIR)
    if backup_dir.exists():
        for path in sorted(backup_dir.glob("*.db"), key=lambda item: item.stat().st_mtime, reverse=True):
            if path.resolve() == active_db_path:
                continue
            if is_valid_sqlite_backup(path):
                candidates.append(path)

    fallback_path = Path(BASE_DIR) / "data" / "acg_ratings.db.bak"
    if fallback_path.exists() and fallback_path.resolve() != active_db_path and is_valid_sqlite_backup(fallback_path):
        candidates.append(fallback_path)

    unique_paths = {}
    for path in candidates:
        unique_paths[path.resolve()] = path

    if not unique_paths:
        return None

    return max(unique_paths.values(), key=lambda item: item.stat().st_mtime)


def rebuild_players_fts_artifacts(conn):
    """Rebuild player search virtual tables and triggers from scratch.

    Some legacy backups include stale FTS objects that pass integrity_check but
    fail on player updates; dropping them forces a clean rebuild by migrations.
    """
    conn.execute("DROP TRIGGER IF EXISTS players_fts_ai")
    conn.execute("DROP TRIGGER IF EXISTS players_fts_ad")
    conn.execute("DROP TRIGGER IF EXISTS players_fts_au")
    conn.execute("DROP TABLE IF EXISTS players_fts")


def ensure_players_fts_artifacts(conn):
    """Create a fresh players FTS index and sync triggers."""
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS players_fts USING fts5(
            id UNINDEXED,
            display_name,
            country,
            club,
            slug,
            content='players',
            content_rowid='id'
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS players_fts_ai AFTER INSERT ON players BEGIN
            INSERT INTO players_fts(rowid, id, display_name, country, club, slug)
            VALUES (new.id, new.id, new.display_name, new.country, new.club, new.slug);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS players_fts_ad AFTER DELETE ON players BEGIN
            INSERT INTO players_fts(players_fts, rowid, id, display_name, country, club, slug)
            VALUES('delete', old.id, old.id, old.display_name, old.country, old.club, old.slug);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS players_fts_au AFTER UPDATE ON players BEGIN
            INSERT INTO players_fts(players_fts, rowid, id, display_name, country, club, slug)
            VALUES('delete', old.id, old.id, old.display_name, old.country, old.club, old.slug);
            INSERT INTO players_fts(rowid, id, display_name, country, club, slug)
            VALUES (new.id, new.id, new.display_name, new.country, new.club, new.slug);
        END
        """
    )
    conn.execute(
        """
        INSERT INTO players_fts(players_fts)
        VALUES('rebuild')
        """
    )


def ensure_rating_state_table(conn):
    """Ensure dirty-date tracking exists for incremental rating updates."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rating_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            earliest_dirty_date TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO rating_state (id, earliest_dirty_date)
        VALUES (1, NULL)
        """
    )


def restore_db_from_backup(path):
    """Restore the canonical database file from the given valid backup and re-run migrations."""
    if path is None or not Path(path).is_file() or not is_valid_sqlite_backup(path):
        return False

    backup_path = Path(path).resolve()
    active_db_path = Path(DB_PATH).resolve()
    if backup_path == active_db_path:
        logger.warning("Skipping backup restore because the selected backup matches the active database: %s", path)
        return False

    shutil.copy2(path, DB_PATH)

    from app import (
        ensure_player_schema_columns,
        migrate_config_schema,
        migrate_application_settings_schema,
        migrate_tournament_schema,
        migrate_matches_notes_schema,
        migrate_tournament_match_identity_schema,
        normalize_match_round_values,
        repair_legacy_players_table,
    )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        repair_legacy_players_table(conn)
        migrate_tournament_schema(conn)
        migrate_config_schema(conn)
        migrate_auth_schema(conn)
        migrate_application_settings_schema(conn)
        bootstrap_default_admin_account(conn)
        rebuild_players_fts_artifacts(conn)
        ensure_player_schema_columns(conn)
        ensure_players_fts_artifacts(conn)
        ensure_rating_state_table(conn)
        migrate_matches_notes_schema(conn)
        migrate_tournament_match_identity_schema(conn)
        normalize_match_round_values(conn)
        conn.commit()
    finally:
        conn.close()

    return True


admin_bp = Blueprint("admin", __name__)

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
    "admin.admin_approve_result_submission": "operator",
    "admin.admin_reject_result_submission": "operator",
    "admin.admin_profile": "results_submitter",
    "admin.admin_report_results": "results_submitter",
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

@admin_bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    lang = get_language(request.args.get("lang"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password")
        client_ip = request.remote_addr or "unknown"

        conn = get_db()
        try:
            user = authenticate_user(username, password, conn=conn)
            if user is not None:
                session.clear()
                session["user_id"] = user["id"]
                session["user_role"] = user.get("role") or "administrator"
                if user.get("language"):
                    session["user_language"] = user["language"]
                if user.get("theme"):
                    session["user_theme"] = user["theme"]
                clear_login_attempts(client_ip)
                log_admin_action(
                    "login",
                    "auth",
                    {"username": user["username"], "role": user.get("role")},
                    user_id=user["id"],
                )
                if user.get("role") == "member":
                    return redirect(url_for("admin_report_results", lang=lang))
                return redirect(url_for("admin", lang=lang))
        finally:
            conn.close()

        if record_failed_login_attempt(client_ip):
            flash(TRANSLATIONS[lang]["invalid_password"])
            return render_template(
                "admin/login.html",
                lang=lang,
                translations=TRANSLATIONS[lang],
            )

        flash(TRANSLATIONS[lang]["invalid_password"])

    return render_template(
        "admin/login.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
    )


@admin_bp.route("/admin/register", methods=["GET", "POST"])
def admin_register():
    lang = get_language(request.args.get("lang"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password") or ""
        email = (request.form.get("email") or "").strip()
        if not verify_recaptcha(
            request.form.get("recaptcha_token"),
            action="register",
            remote_ip=request.remote_addr,
        ):
            flash(TRANSLATIONS[lang]["recaptcha_failed"])
        elif len(password) < 8:
            flash(TRANSLATIONS[lang]["password_too_short"])
        elif password != confirm_password:
            flash(TRANSLATIONS[lang]["password_mismatch"])
        else:
            try:
                create_user_account(
                    username,
                    password,
                    role_name="member",
                    email=email,
                )
                flash(TRANSLATIONS[lang]["account_created_success"])
                return redirect(url_for("admin_login", lang=lang))
            except ValueError as exc:
                message_key = {
                    "username already exists": "user_username_taken",
                    "email already exists": "email_taken",
                    "invalid email": "invalid_email",
                }.get(str(exc), "error")
                flash(TRANSLATIONS[lang][message_key])

    return render_template(
        "admin/register.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        recaptcha_site_key=RECAPTCHA_SITE_KEY,
    )


@admin_bp.route("/admin/report-results", methods=["GET", "POST"])
def admin_report_results():
    permission_error = require_permission("results_submitter")
    if permission_error is not None:
        return permission_error

    lang = get_language(request.args.get("lang"))
    user = get_current_user()
    conn = get_db()
    try:
        if user and user.get("player_id") is not None:
            linked_player = conn.execute(
                "SELECT display_name FROM players WHERE id = ?",
                (user["player_id"],),
            ).fetchone()
            user["player_name"] = linked_player["display_name"] if linked_player else ""

        if request.method == "POST":
            player_id = user.get("player_id") if user else None
            opponent_id = request.form.get("opponent_player_id")
            color = request.form.get("color", "white")
            result = (request.form.get("result") or "").strip()
            match_date = (request.form.get("match_date") or "").strip()
            event = (request.form.get("event") or "").strip()
            notes = (request.form.get("notes") or "").strip()
            if player_id is None:
                flash(TRANSLATIONS[lang]["player_link_required"])
            elif color not in {"white", "black"}:
                flash(TRANSLATIONS[lang]["error"])
            else:
                white_player_id = player_id if color == "white" else opponent_id
                black_player_id = opponent_id if color == "white" else player_id
                valid, message = validate_match_form_data(
                    conn,
                    match_date,
                    white_player_id,
                    black_player_id,
                    result,
                    lang,
                )
                try:
                    handicap_stones = parse_handicap_stones(request.form.get("handicap_stones"))
                except ValueError:
                    valid = False
                    message = TRANSLATIONS[lang]["error"]
                if valid:
                    duplicate = conn.execute(
                        """
                        SELECT 1 FROM result_submissions
                        WHERE submitted_by_user_id = ? AND status = 'pending'
                          AND match_date = ? AND white_player_id = ?
                          AND black_player_id = ? AND result = ?
                        """,
                        (user["id"], match_date, white_player_id, black_player_id, result),
                    ).fetchone()
                    if duplicate is not None:
                        valid = False
                        message = TRANSLATIONS[lang]["duplicate_submission"]
                if valid:
                    conn.execute(
                        """
                        INSERT INTO result_submissions
                            (submitted_by_user_id, match_date, white_player_id, black_player_id,
                             result, event, notes, handicap_stones)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (user["id"], match_date, white_player_id, black_player_id, result, event, notes, handicap_stones),
                    )
                    conn.commit()
                    log_admin_action(
                        "result_submitted",
                        "result_submission",
                        {"submission_id": conn.execute("SELECT last_insert_rowid()").fetchone()[0]},
                        user_id=user["id"],
                    )
                    flash(TRANSLATIONS[lang]["result_submitted_success"])
                    return redirect(url_for("admin_report_results", lang=lang))
                flash(message or TRANSLATIONS[lang]["error"])

        opponents = []
        if user and user.get("player_id") is not None:
            opponents = conn.execute(
                "SELECT id, display_name FROM players WHERE id != ? ORDER BY display_name",
                (user["player_id"],),
            ).fetchall()
        submissions = conn.execute(
            """
            SELECT rs.*, p_white.display_name AS white_name, p_black.display_name AS black_name
            FROM result_submissions rs
            JOIN players p_white ON p_white.id = rs.white_player_id
            JOIN players p_black ON p_black.id = rs.black_player_id
            WHERE rs.submitted_by_user_id = ?
            ORDER BY rs.created_at DESC, rs.id DESC
            """,
            (user["id"],),
        ).fetchall()
    finally:
        conn.close()

    return render_template(
        "admin/report_results.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        user=user,
        opponents=opponents,
        submissions=submissions,
    )


@admin_bp.route("/admin/result-submissions")
def admin_result_submissions():
    permission_error = require_permission("operator")
    if permission_error is not None:
        return permission_error

    lang = get_language(request.args.get("lang"))
    status = request.args.get("status", "pending")
    if status not in {"pending", "approved", "rejected", "all"}:
        status = "pending"
    conn = get_db()
    try:
        query = """
            SELECT rs.*, submitter.username AS submitter_username,
                   p_white.display_name AS white_name, p_black.display_name AS black_name,
                   reviewer.username AS reviewer_username
            FROM result_submissions rs
            JOIN users submitter ON submitter.id = rs.submitted_by_user_id
            JOIN players p_white ON p_white.id = rs.white_player_id
            JOIN players p_black ON p_black.id = rs.black_player_id
            LEFT JOIN users reviewer ON reviewer.id = rs.reviewed_by_user_id
        """
        params = []
        if status != "all":
            query += " WHERE rs.status = ?"
            params.append(status)
        query += " ORDER BY rs.created_at DESC, rs.id DESC"
        submissions = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    return render_template(
        "admin/result_submissions.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        submissions=submissions,
        selected_status=status,
    )


@admin_bp.route("/admin/result-submissions/<int:submission_id>/approve", methods=["POST"])
def admin_approve_result_submission(submission_id):
    permission_error = require_permission("operator")
    if permission_error is not None:
        return permission_error
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    try:
        submission = conn.execute(
            "SELECT * FROM result_submissions WHERE id = ? AND status = 'pending'",
            (submission_id,),
        ).fetchone()
        if submission is None:
            flash(TRANSLATIONS[lang]["submission_not_pending"])
            return redirect(url_for("admin_result_submissions", lang=lang))
        now = current_timestamp()
        match_id = conn.execute(
            """
            INSERT INTO matches
                (match_date, white_player_id, black_player_id, result, event, notes, round_number, handicap_stones)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (submission["match_date"], submission["white_player_id"], submission["black_player_id"],
             submission["result"], submission["event"], submission["notes"], submission["round_number"],
             submission["handicap_stones"]),
        ).lastrowid
        conn.execute(
            """
            UPDATE result_submissions
            SET status = 'approved', reviewed_by_user_id = ?, reviewed_at = ?, review_notes = ?
            WHERE id = ? AND status = 'pending'
            """,
            (session.get("user_id"), now, request.form.get("review_notes", "").strip(), submission_id),
        )
        conn.commit()
    except sqlite3.DatabaseError:
        conn.rollback()
        logger.exception("Could not approve result submission %s", submission_id)
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_result_submissions", lang=lang))
    finally:
        conn.close()
    refresh_stats()
    mark_dirty(submission["match_date"])
    update_from_latest_snapshot()
    log_admin_action(
        "result_submission_approved",
        "result_submission",
        {"submission_id": submission_id, "match_id": match_id},
        user_id=session.get("user_id"),
    )
    flash(TRANSLATIONS[lang]["submission_approved"])
    return redirect(url_for("admin_result_submissions", lang=lang))


@admin_bp.route("/admin/result-submissions/<int:submission_id>/reject", methods=["POST"])
def admin_reject_result_submission(submission_id):
    permission_error = require_permission("operator")
    if permission_error is not None:
        return permission_error
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    try:
        updated = conn.execute(
            """
            UPDATE result_submissions
            SET status = 'rejected', reviewed_by_user_id = ?, reviewed_at = ?, review_notes = ?
            WHERE id = ? AND status = 'pending'
            """,
            (session.get("user_id"), current_timestamp(), request.form.get("review_notes", "").strip(), submission_id),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    if not updated:
        flash(TRANSLATIONS[lang]["submission_not_pending"])
    else:
        log_admin_action(
            "result_submission_rejected",
            "result_submission",
            {"submission_id": submission_id},
            user_id=session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["submission_rejected"])
    return redirect(url_for("admin_result_submissions", lang=lang))


@admin_bp.route("/admin/settings", methods=["GET", "POST"])
def admin_settings():
    permission_error = require_permission("admin")
    if permission_error is not None:
        return permission_error

    lang = get_language(request.args.get("lang"))
    settings = get_application_settings()
    if request.method == "POST":
        action = request.form.get("action")
        values = DEFAULT_APPLICATION_SETTINGS if action == "reset" else {
            "max_login_attempts": request.form.get("max_login_attempts"),
            "login_window_seconds": request.form.get("login_window_seconds"),
            "password_reset_ttl_seconds": request.form.get("password_reset_ttl_seconds"),
        }
        try:
            settings = update_application_settings(values)
        except ValueError:
            flash(TRANSLATIONS[lang]["invalid_application_settings"])
        else:
            log_admin_action(
                "application_settings_reset" if action == "reset" else "application_settings_updated",
                "application_settings",
                settings,
                user_id=session.get("user_id"),
            )
            flash(
                TRANSLATIONS[lang]["reset_to_default_success"]
                if action == "reset"
                else TRANSLATIONS[lang]["success"]
            )
            return redirect(url_for("admin_settings", lang=lang))

    return render_template(
        "admin/settings.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        settings=settings,
    )


@admin_bp.route("/admin/profile", methods=["GET", "POST"])
def admin_profile():
    permission_error = require_permission("results_submitter")
    if permission_error is not None:
        return permission_error

    lang = get_language(request.args.get("lang"))
    user = get_current_user()
    if user is None:
        return redirect(url_for("admin_login", lang=lang))
    timezone_choices = get_timezone_choices()
    timezone_labels = {
        timezone: format_timezone_label(timezone)
        for timezone in timezone_choices
    }

    if request.method == "POST":
        if request.form.get("logout") == "1":
            log_admin_action(
                "logout",
                "auth",
                {"status": "success"},
                user_id=user["id"],
            )
            session.clear()
            return redirect(url_for("index", lang=lang))

        email = (request.form.get("email") or "").strip()
        language = (request.form.get("language") or "").strip()
        theme = (request.form.get("theme") or "").strip()
        timezone_name = (request.form.get("timezone") or "").strip()
        current_password = request.form.get("current_password") or ""
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        try:
            email = validate_email_address(email) if email else None
            timezone_name = validate_timezone(timezone_name)
            validate_theme(theme)
        except ValueError as exc:
            message_key = {
                "invalid email": "invalid_email",
                "unsupported timezone": "invalid_timezone",
                "unsupported theme": "invalid_theme",
            }.get(str(exc), "error")
            flash(TRANSLATIONS[lang][message_key])
            user.update(email=email, language=language, theme=theme, timezone=timezone_name)
            return render_template(
                "admin/profile.html",
                lang=lang,
                translations=TRANSLATIONS[lang],
                user=user,
                languages=LANGUAGE_CHOICES,
                themes=THEME_CHOICES,
                timezone_choices=timezone_choices,
                timezone_labels=timezone_labels,
            )

        if language not in LANGUAGE_CHOICES:
            flash(TRANSLATIONS[lang]["invalid_language"])
            return redirect(url_for("admin_profile", lang=lang))

        password_hash = None
        if current_password or new_password or confirm_password:
            if not check_password_hash(user["password_hash"], current_password):
                flash(TRANSLATIONS[lang]["current_password_invalid"])
                return redirect(url_for("admin_profile", lang=lang))
            if len(new_password) < 8:
                flash(TRANSLATIONS[lang]["password_too_short"])
                return redirect(url_for("admin_profile", lang=lang))
            if new_password != confirm_password:
                flash(TRANSLATIONS[lang]["password_mismatch"])
                return redirect(url_for("admin_profile", lang=lang))
            password_hash = generate_password_hash(new_password)

        conn = get_db()
        try:
            duplicate = conn.execute(
                "SELECT id FROM users WHERE email = ? AND id != ?",
                (email, user["id"]),
            ).fetchone() if email else None
            if duplicate is not None:
                flash(TRANSLATIONS[lang]["email_taken"])
                return redirect(url_for("admin_profile", lang=lang))
            if password_hash:
                conn.execute(
                    "UPDATE users SET email = ?, language = ?, theme = ?, timezone = ?, password_hash = ? WHERE id = ?",
                    (email, language, theme, timezone_name, password_hash, user["id"]),
                )
            else:
                conn.execute(
                    "UPDATE users SET email = ?, language = ?, theme = ?, timezone = ? WHERE id = ?",
                    (email, language, theme, timezone_name, user["id"]),
                )
            conn.commit()
        finally:
            conn.close()
        session["user_language"] = language
        session["user_theme"] = theme
        log_admin_action(
            "profile_updated",
            "user",
            {"email_changed": email != user.get("email"), "password_changed": bool(password_hash)},
            user_id=user["id"],
        )
        flash(TRANSLATIONS[lang]["profile_updated_success"])
        return redirect(url_for("admin_profile", lang=language))

    return render_template(
        "admin/profile.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        user=user,
        languages=LANGUAGE_CHOICES,
        themes=THEME_CHOICES,
        timezone_choices=timezone_choices,
        timezone_labels=timezone_labels,
    )


@admin_bp.route("/admin/forgot-password", methods=["GET", "POST"])
@admin_bp.route("/admin/forgot_password", methods=["GET", "POST"])
def admin_forgot_password():
    lang = get_language(request.args.get("lang"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        conn = get_db()
        try:
            user = conn.execute(
                "SELECT id, email FROM users WHERE lower(email) = ? AND is_active = 1",
                (email,),
            ).fetchone() if email else None
            if user is not None and user["email"]:
                token = create_password_reset_token(user["id"], conn=conn)
                conn.commit()
                reset_url = url_for(
                    "admin.admin_reset_password",
                    token=token,
                    lang=lang,
                    _external=True,
                )
                try:
                    send_password_reset_email(user["email"], reset_url)
                except Exception:
                    logger.exception("Password reset email delivery failed")
        finally:
            conn.close()
        flash(TRANSLATIONS[lang]["password_reset_requested"])
    return render_template(
        "admin/forgot_password.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
    )


@admin_bp.route("/admin/reset-password/<token>", methods=["GET", "POST"])
def admin_reset_password(token):
    lang = get_language(request.args.get("lang"))
    if request.method == "POST":
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""
        if len(new_password) < 8:
            flash(TRANSLATIONS[lang]["password_too_short"])
        elif new_password != confirm_password:
            flash(TRANSLATIONS[lang]["password_mismatch"])
        else:
            if reset_password_with_token(token, new_password):
                flash(TRANSLATIONS[lang]["password_reset_success"])
                return redirect(url_for("admin_login", lang=lang))
            flash(TRANSLATIONS[lang]["invalid_reset_token"])
    return render_template(
        "admin/reset_password.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        token=token,
    )

@admin_bp.route("/admin/logout")
def admin_logout():
    current_user_id = session.get("user_id")
    log_admin_action(
        "logout",
        "auth",
        {"status": "success"},
        user_id=current_user_id,
    )
    session.clear()

    return redirect(
        url_for("index")
    )

@admin_bp.route("/import")
def import_matches():
    lang = get_language(request.args.get("lang"))
    return redirect(
        url_for("admin_import", lang=lang)
    )

@admin_bp.route("/admin/import", methods=["GET", "POST"])
def admin_import():
    permission_error = require_permission("operator")
    if permission_error is not None:
        return permission_error

    lang = get_language(request.args.get("lang"))
    preview = None
    preview_file = request.form.get("preview_file") or request.args.get("preview_file")

    if request.method == "POST":
        action = request.form.get("action")
        file = request.files.get("file")

        if action == "commit_preview" and preview_file:
            upload_path = os.path.join(BASE_DIR, "uploads", preview_file)
            try:
                player_decisions = {
                    key.removeprefix("player_decision_"): value
                    for key, value in request.form.items()
                    if key.startswith("player_decision_")
                }
                metadata_overrides = {
                    "name": (request.form.get("metadata_name") or "").strip(),
                    "description": (request.form.get("metadata_description") or "").strip(),
                    "short_name": (request.form.get("metadata_short_name") or "").strip(),
                    "location": (request.form.get("metadata_location") or "").strip(),
                    "begin_date": (request.form.get("metadata_begin_date") or "").strip(),
                    "end_date": (request.form.get("metadata_end_date") or "").strip(),
                    "rounds": request.form.get("metadata_rounds", type=int),
                    "pairing_system": normalize_tournament_system(
                        request.form.get("metadata_tournament_type")
                        or request.form.get("metadata_pairing_system")
                    ),
                }
                if metadata_overrides["pairing_system"] == "accelerated_swiss":
                    metadata_overrides["acceleration_scheme"] = acceleration_scheme_from_form(request.form)
                    metadata_rounds = request.form.get("metadata_rounds", type=int) or 1
                    metadata_overrides["acceleration_rounds"] = request.form.get(
                        "metadata_acceleration_rounds",
                        default_acceleration_rounds(metadata_rounds),
                        type=int,
                    )
                if metadata_overrides["pairing_system"] == "swiss_cat":
                    metadata_overrides["category_rounds"] = request.form.get(
                        "metadata_category_rounds", DEFAULT_CATEGORY_ROUNDS, type=int
                    )
                if metadata_overrides["pairing_system"] == "mcmahon":
                    metadata_overrides["mm_bar"], metadata_overrides["mm_floor"], metadata_overrides["mm_zero"] = validate_mcmahon_settings(
                        request.form.get("metadata_mm_bar"),
                        request.form.get("metadata_mm_floor"),
                        request.form.get("metadata_mm_zero"),
                    )
                metadata_overrides = {key: value for key, value in metadata_overrides.items() if value not in (None, "")}
                if "metadata_description" in request.form:
                    metadata_overrides["description"] = (request.form.get("metadata_description") or "").strip()
                conn = get_db()
                try:
                    if request.form.get("metadata_decision") == "reject":
                        raise ValueError("Import rejected during metadata review")
                    tournament_id, metadata, matched = create_tournament_from_gotha(
                        conn, upload_path, player_decisions=player_decisions,
                        metadata_overrides=metadata_overrides,
                    )
                    conn.commit()
                finally:
                    conn.close()
                flash(f"{TRANSLATIONS[lang]['success']} ({matched} players)")
                return redirect(url_for("admin_tournament", tournament_id=tournament_id, lang=lang))
            except (OSError, ValueError, sqlite3.DatabaseError) as exc:
                flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
                return redirect(url_for("admin_import", lang=lang))

        if not file or file.filename == "":
            flash(TRANSLATIONS[lang]["no_file"])
            return redirect(url_for("import_matches", lang=lang))

        filename = secure_filename(file.filename or "")
        upload_path = os.path.join(BASE_DIR, "uploads", filename)
        os.makedirs(os.path.dirname(upload_path), exist_ok=True)
        file.save(upload_path)

        try:
            extension = Path(filename).suffix.lower()

            if extension in (".xlsx", ".xls"):
                stats = import_workbook_data(upload_path, reset=True)
                run_post_import_replay()
                flash(
                    f"{TRANSLATIONS[lang]['success']} "
                    f"({stats['players']} players, "
                    f"{stats['matches']} matches)"
                )
                return redirect(url_for("import_matches", lang=lang))

            if extension == ".xml":
                conn = get_db()
                try:
                    preview = build_import_preview(conn, upload_path)
                finally:
                    conn.close()
                return render_template(
                    "admin/import.html",
                    lang=lang,
                    translations=TRANSLATIONS[lang],
                    preview=preview,
                    preview_file=filename,
                    acceleration_scheme_options=ACCELERATION_SCHEMES,
                    acceleration_scheme_choice=acceleration_scheme_choice(preview["metadata"].get("acceleration_scheme")),
                )

            if extension == ".csv":
                with open(upload_path, newline="", encoding="utf-8-sig") as csv_file:
                    reader = csv.DictReader(csv_file)
                    required_columns = {"date", "white", "black", "result"}
                    columns = {col.strip().lower() for col in reader.fieldnames or []}

                    if not required_columns.issubset(columns):
                        raise ValueError(TRANSLATIONS[lang]["required_columns_missing"])

                    conn = get_db()
                    try:
                        players = conn.execute("SELECT id, display_name FROM players").fetchall()
                        player_lookup = {normalize_key(row["display_name"]): row["id"] for row in players}
                        imported_matches = 0
                        earliest_match_date = None

                        for row in reader:
                            white_name = str(row.get("white", "")).strip()
                            black_name = str(row.get("black", "")).strip()
                            white_id = player_lookup.get(normalize_key(white_name))
                            black_id = player_lookup.get(normalize_key(black_name))

                            if white_id is None or black_id is None:
                                continue

                            match_date = parse_date_value(row.get("date", ""))
                            # Optional "handicap" column: number of stones given
                            # to Black. Missing/blank/invalid values default to
                            # 0 (no handicap) rather than rejecting the row --
                            # handicap data is an enhancement to existing CSV
                            # imports, not a new required column.
                            try:
                                handicap_stones = int(str(row.get("handicap", "") or "0").strip() or 0)
                            except ValueError:
                                handicap_stones = 0
                            handicap_stones = max(0, min(9, handicap_stones))
                            conn.execute(
                                """
                                INSERT INTO matches
                                (
                                    match_date,
                                    white_player_id,
                                    black_player_id,
                                    result,
                                    event,
                                    notes,
                                    round_number,
                                    handicap_stones
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    match_date,
                                    white_id,
                                    black_id,
                                    row.get("result", ""),
                                    "Imported",
                                    normalize_round_note_for_storage(row.get("notes", row.get("round", ""))),
                                    normalize_round_note(row.get("notes", row.get("round", ""))),
                                    handicap_stones,
                                ),
                            )
                            imported_matches += 1
                            if earliest_match_date is None or match_date < earliest_match_date:
                                earliest_match_date = match_date

                        conn.commit()
                    finally:
                        conn.close()

                if earliest_match_date:
                    mark_dirty(earliest_match_date)
                    run_post_import_replay()
                else:
                    refresh_stats()

                flash(f"{TRANSLATIONS[lang]['success']} ({imported_matches} matches)")
                return redirect(url_for("import_matches", lang=lang))

            raise ValueError(TRANSLATIONS[lang]["unsupported_file_format"])

        except Exception as exc:
            flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
            return redirect(url_for("import_matches", lang=lang))

    return render_template(
        "admin/import.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        preview=preview,
        preview_file=preview_file,
        acceleration_scheme_options=ACCELERATION_SCHEMES,
        acceleration_scheme_choice=acceleration_scheme_choice(
            preview["metadata"].get("acceleration_scheme") if preview else None
        ),
    )


@admin_bp.route("/admin/matches")
def admin_matches():
    permission_error = require_permission("operator")
    if permission_error is not None:
        return permission_error

    lang = get_language(request.args.get("lang"))
    page_raw = request.args.get("page")
    page_size_raw = request.args.get("page_size")
    sort_key = _parse_match_sort(request.args.get("sort"))
    sort_order = _parse_match_order(request.args.get("order"))

    try:
        page = int(page_raw) if page_raw is not None else 1
    except (TypeError, ValueError):
        return Response("Invalid page parameter", status=400)
    if page <= 0:
        return Response("page must be positive", status=400)

    try:
        page_size = int(page_size_raw) if page_size_raw is not None else 25
    except (TypeError, ValueError):
        return Response("Invalid page_size parameter", status=400)
    if page_size <= 0:
        return Response("page_size must be positive", status=400)
    page_size = min(page_size, 100)
    date_from, date_to, player_id = _parse_match_filters(request.args)
    filter_sql, filter_params = _match_filter_sql(date_from, date_to, player_id)

    conn = get_db()
    total_count = conn.execute(
        f"SELECT COUNT(*) FROM matches m {filter_sql}",
        filter_params,
    ).fetchone()[0]
    page_details = pagination_details(total_count, page, page_size)
    page = page_details["page"]
    page_size = page_details["page_size"]

    order_clause = MATCH_SORT_FIELDS[sort_key]
    order_sql = f"ORDER BY {order_clause} {sort_order.upper()}, m.match_date DESC, m.round_number DESC, m.id DESC"

    match_rows = conn.execute(
        f"""
        SELECT
            m.id,
            m.match_date,
            m.notes,
            m.round_number,
            m.event,
            p_white.id AS white_id,
            p_black.id AS black_id,
            p_white.display_name AS white_name,
            p_black.display_name AS black_name,
            m.result
        FROM matches m
        JOIN players p_white ON p_white.id = m.white_player_id
        JOIN players p_black ON p_black.id = m.black_player_id
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
        "admin/matches.html",
        matches=match_rows,
        lang=lang,
        translations=TRANSLATIONS[lang],
        total_count=total_count,
        sort=sort_key,
        order=sort_order,
        date_from=date_from,
        date_to=date_to,
        player_id=player_id,
        match_players=match_players,
        **page_details,
    )

@admin_bp.route("/admin/tournaments", methods=["GET", "POST"])
def admin_tournaments():
    if not admin_required():
        return redirect(
            url_for("admin_login", lang=get_language(request.args.get("lang")))
        )

    lang = get_language(request.args.get("lang"))
    translations = TRANSLATIONS[lang]
    conn = get_db()

    if request.method == "POST":
        action = request.form.get("action")
        pairing_system = normalize_tournament_system(
            request.form.get("tournament_type")
            or request.form.get("pairing_system", "swiss")
        )
        if action == "import_opengotha":
            file = request.files.get("file")
            if not file or not (file.filename or "").lower().endswith(".xml"):
                flash(translations["no_file"])
            else:
                filename = secure_filename(file.filename or "")
                upload_path = os.path.join(BASE_DIR, "uploads", filename)
                file.save(upload_path)
                try:
                    tournament_id, metadata, matched = create_tournament_from_gotha(
                        conn, upload_path
                    )
                    log_admin_action(
                        "tournament_imported",
                        "tournament",
                        {"tournament_id": tournament_id, "filename": filename, "matched_players": matched},
                        user_id=session.get("user_id"),
                    )
                    flash(f"{translations['success']} ({matched} players)")
                    conn.close()
                    return redirect(url_for("admin_tournament", tournament_id=tournament_id, lang=lang))
                except (OSError, ValueError) as exc:
                    flash(f"{translations['error']}: {exc}")
                except sqlite3.DatabaseError as exc:
                    conn.rollback()
                    logger.exception("OpenGotha tournament import failed for %s", upload_path)
                    flash(f"{translations['error']}: {exc}")
                    conn.close()
                    return render_template(
                        "admin/tournaments.html",
                        tournaments=[],
                        systems=SUPPORTED_SYSTEMS,
                        lang=lang,
                        translations=translations,
                    )
        elif action == "create" and pairing_system in SUPPORTED_SYSTEMS:
            name = request.form.get("name", "").strip()
            if not name:
                flash(translations["error"])
            else:
                rounds = normalize_tournament_rounds(request.form.get("rounds", 1, type=int))
                bye_points = request.form.get("bye_points", 1.0, type=float)
                absent_points = request.form.get("absent_points", 0.0, type=float)
                handicap_enabled = 1 if request.form.get("handicap_enabled") == "1" else 0
                try:
                    acceleration_scheme = (
                        acceleration_scheme_from_form(request.form)
                        if pairing_system == "accelerated_swiss"
                        else DEFAULT_ACCELERATION_SCHEME
                    )
                except (TypeError, ValueError):
                    acceleration_scheme = None
                if (
                    bye_points not in {0.0, 0.5, 1.0}
                    or absent_points not in {0.0, 0.5, 1.0}
                    or acceleration_scheme is None
                ):
                    flash(translations["error"])
                    bye_points = absent_points = None
                if bye_points is None:
                    tournaments = conn.execute("SELECT * FROM tournaments ORDER BY id DESC").fetchall()
                    conn.close()
                    return render_template(
                        "admin/tournaments.html",
                        tournaments=tournaments,
                        systems=SUPPORTED_SYSTEMS,
                        lang=lang,
                        translations=translations,
                    )
                tournament_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(tournaments)").fetchall()
                }
                tournament_values = [
                    name,
                    request.form.get("location", "").strip(),
                    rounds,
                    pairing_system,
                    pairing_system,
                    bye_points,
                    absent_points,
                ]
                insert_columns = [
                    "name", "location", "rounds", "tournament_type", "pairing_system",
                    "bye_points", "absent_points",
                ]
                if "description" in tournament_columns:
                    insert_columns.insert(1, "description")
                    tournament_values.insert(1, request.form.get("description", "").strip())
                if "acceleration_scheme" in tournament_columns:
                    insert_columns.append("acceleration_scheme")
                    tournament_values.append(acceleration_scheme)
                if pairing_system == "accelerated_swiss" and "acceleration_rounds" in tournament_columns:
                    insert_columns.append("acceleration_rounds")
                    tournament_values.append(default_acceleration_rounds(rounds))
                if "handicap_enabled" in tournament_columns:
                    insert_columns.append("handicap_enabled")
                    tournament_values.append(handicap_enabled)
                insert_columns.extend(["status", "created_at"])
                tournament_values.extend(["draft", current_timestamp()])
                placeholders = ", ".join("?" for _ in insert_columns)
                conn.execute(
                    f"INSERT INTO tournaments ({', '.join(insert_columns)}) VALUES ({placeholders})",
                    tournament_values,
                )
                tournament_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.commit()
                log_admin_action(
                    "tournament_created",
                    "tournament",
                    {"tournament_id": tournament_id, "name": name, "pairing_system": pairing_system},
                    user_id=session.get("user_id"),
                )
                flash(translations["success"])
                conn.close()
                return redirect(url_for("admin_tournament", tournament_id=tournament_id, lang=lang))
        elif action == "create":
            flash(translations["error"])

    sort_key = parse_tournament_sort(request.args.get("sort"))
    sort_order = parse_tournament_order(request.args.get("order"))
    participant_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('tournament_participants', 'tournament_pending_players')"
        ).fetchall()
    }
    if sort_key == "participants" and {"tournament_participants", "tournament_pending_players"}.issubset(participant_tables):
        participant_sort_expr = "(SELECT COUNT(*) FROM tournament_participants WHERE tournament_id = tournaments.id) + (SELECT COUNT(*) FROM tournament_pending_players WHERE tournament_id = tournaments.id)"
    else:
        participant_sort_expr = "0"
    page = parse_page_number(request.args.get("page"), default=1)
    page_size = parse_page_size(request.args.get("page_size"), default=25)
    total_count = conn.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0]
    page_details = pagination_details(total_count, page, page_size)
    page = page_details["page"]
    page_size = page_details["page_size"]
    tournaments = conn.execute(
        f"SELECT * FROM tournaments ORDER BY {TOURNAMENT_SORT_FIELDS[sort_key] if sort_key != 'participants' else participant_sort_expr} {sort_order.upper()}, id DESC LIMIT ? OFFSET ?",
        (page_size, (page - 1) * page_size),
    ).fetchall()
    conn.close()
    return render_template(
        "admin/tournaments.html",
        tournaments=tournaments,
        systems=SUPPORTED_SYSTEMS,
        lang=lang,
        translations=translations,
        sort=sort_key,
        order=sort_order,
        total_count=total_count,
        **page_details,
    )

@admin_bp.route("/admin/tournaments/<int:tournament_id>/delete", methods=["POST"])
def admin_delete_tournament(tournament_id):
    if not admin_required():
        return redirect(
            url_for("admin_login", lang=get_language(request.args.get("lang")))
        )
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    try:
        delete_tournament(conn, tournament_id)
        log_admin_action(
            "tournament_deleted",
            "tournament",
            {"tournament_id": tournament_id},
            user_id=session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["success"])
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect(url_for("admin_tournaments", lang=lang))

@admin_bp.route("/admin/tournaments/<int:tournament_id>/status", methods=["POST"])
def admin_update_tournament_status(tournament_id):
    if not admin_required():
        return redirect(
            url_for("admin_login", lang=get_language(request.args.get("lang")))
        )
    lang = get_language(request.args.get("lang"))
    status = request.form.get("status", "").strip()
    if status not in TOURNAMENT_STATUSES:
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_tournaments", lang=lang))

    conn = get_db()
    try:
        tournament_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(tournaments)").fetchall()
        }
        if "handicap_enabled" not in tournament_columns:
            conn.execute(
                "ALTER TABLE tournaments ADD COLUMN handicap_enabled INTEGER NOT NULL DEFAULT 0"
            )
        updated = conn.execute(
            "UPDATE tournaments SET status = ? WHERE id = ?",
            (status, tournament_id),
        ).rowcount
        if not updated:
            flash(TRANSLATIONS[lang]["error"])
        else:
            conn.commit()
            log_admin_action(
                "tournament_status_updated",
                "tournament",
                {"tournament_id": tournament_id, "status": status},
                user_id=session.get("user_id"),
            )
            flash(TRANSLATIONS[lang]["success"])
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        logger.exception("Tournament settings update failed for %s", tournament_id)
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect_or_json(url_for("admin_tournament", tournament_id=tournament_id, lang=lang))

@admin_bp.route("/admin/tournaments/<int:tournament_id>/settings", methods=["POST"])
def admin_update_tournament_settings(tournament_id):
    if not admin_required():
        return redirect(
            url_for("admin_login", lang=get_language(request.args.get("lang")))
        )
    lang = get_language(request.args.get("lang"))
    name = request.form.get("name", "").strip()
    location = request.form.get("location", "").strip()
    description = request.form.get("description")
    begin_date = request.form.get("begin_date")
    end_date = request.form.get("end_date")
    rounds = normalize_tournament_rounds(request.form.get("rounds", 1, type=int))
    bye_points = request.form.get("bye_points", type=float)
    absent_points = request.form.get("absent_points", type=float)
    handicap_enabled = request.form.get("handicap_enabled") == "1"
    apply_auto_handicap = request.form.get("apply_auto_handicap") == "1"
    number_of_categories = request.form.get("number_of_categories")
    category_floors = request.form.getlist("category_floor")
    acceleration_rounds = request.form.get("acceleration_rounds")
    category_rounds = request.form.get("category_rounds")
    mm_bar = request.form.get("mm_bar")
    mm_floor = request.form.get("mm_floor")
    mm_zero = request.form.get("mm_zero")

    if not name or bye_points not in {0.0, 0.5, 1.0} or absent_points not in {0.0, 0.5, 1.0}:
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_tournament_settings", tournament_id=tournament_id, lang=lang))

    conn = get_db()
    tournament_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(tournaments)").fetchall()
    }
    optional_columns = [
        column for column in (
            "description", "begin_date", "end_date", "acceleration_scheme", "acceleration_rounds", "category_rounds",
            "mm_bar", "mm_floor", "mm_zero"
        ) if column in tournament_columns
    ]
    optional_select = f", {', '.join(optional_columns)}" if optional_columns else ""
    try:
        current_tournament = conn.execute(
            f"SELECT pairing_system, tournament_type{optional_select} FROM tournaments WHERE id = ?",
            (tournament_id,),
        ).fetchone()
        if current_tournament is None:
            flash(TRANSLATIONS[lang]["error"])
            return redirect(url_for("admin_tournaments", lang=lang))

        if begin_date is None:
            begin_date = current_tournament["begin_date"] if "begin_date" in current_tournament.keys() else ""
        if end_date is None:
            end_date = current_tournament["end_date"] if "end_date" in current_tournament.keys() else ""
        if description is None:
            description = current_tournament["description"] if "description" in current_tournament.keys() else ""
        try:
            begin_date = parse_date_value(begin_date) if begin_date else None
            end_date = parse_date_value(end_date) if end_date else None
            if begin_date and end_date and begin_date > end_date:
                raise ValueError
        except ValueError:
            flash(TRANSLATIONS[lang]["error"])
            return redirect(url_for("admin_tournament_settings", tournament_id=tournament_id, lang=lang))

        pairing_system = normalize_tournament_system(current_tournament["pairing_system"])
        acceleration_scheme = (
            current_tournament["acceleration_scheme"]
            if "acceleration_scheme" in tournament_columns
            else DEFAULT_ACCELERATION_SCHEME
        ) or DEFAULT_ACCELERATION_SCHEME
        if pairing_system == "accelerated_swiss":
            try:
                acceleration_scheme = acceleration_scheme_from_form(request.form)
            except (TypeError, ValueError):
                flash(TRANSLATIONS[lang]["error"])
                return redirect(url_for("admin_tournament_settings", tournament_id=tournament_id, lang=lang))
        if pairing_system == "mcmahon":
            try:
                mm_bar_value, mm_floor_value, mm_zero_value = validate_mcmahon_settings(mm_bar, mm_floor, mm_zero)
            except (TypeError, ValueError):
                flash(TRANSLATIONS[lang]["error"])
                return redirect(url_for("admin_tournament_settings", tournament_id=tournament_id, lang=lang))

        try:
            acceleration_rounds = (
                default_acceleration_rounds(rounds)
                if acceleration_rounds in (None, "")
                else int(acceleration_rounds)
            )
            category_rounds = (
                DEFAULT_CATEGORY_ROUNDS
                if category_rounds in (None, "")
                else int(category_rounds)
            )
            if acceleration_rounds < 0 or category_rounds < 0 or acceleration_rounds > rounds or category_rounds > rounds:
                raise ValueError
        except (TypeError, ValueError):
            flash(TRANSLATIONS[lang]["error"])
            return redirect(url_for("admin_tournament_settings", tournament_id=tournament_id, lang=lang))

        update_fields = [
            "name = ?", "location = ?", "rounds = ?", "bye_points = ?",
            "absent_points = ?", "handicap_enabled = ?", "tournament_type = ?", "pairing_system = ?",
        ]
        update_values = [name, location, rounds, bye_points, absent_points, int(handicap_enabled), pairing_system, pairing_system]
        if "begin_date" in tournament_columns:
            update_fields.append("begin_date = ?")
            update_values.append(begin_date)
        if "end_date" in tournament_columns:
            update_fields.append("end_date = ?")
            update_values.append(end_date)
        if "description" in tournament_columns:
            update_fields.append("description = ?")
            update_values.append(description)
        if pairing_system == "accelerated_swiss" and "acceleration_scheme" in tournament_columns:
            update_fields.append("acceleration_scheme = ?")
            update_values.append(acceleration_scheme)
        if pairing_system == "accelerated_swiss" and "acceleration_rounds" in tournament_columns:
            update_fields.append("acceleration_rounds = ?")
            update_values.append(acceleration_rounds)
        if pairing_system == "swiss_cat" and "category_rounds" in tournament_columns:
            update_fields.append("category_rounds = ?")
            update_values.append(category_rounds)
        if pairing_system == "mcmahon" and {"mm_bar", "mm_floor", "mm_zero"}.issubset(tournament_columns):
            update_fields.extend(["mm_bar = ?", "mm_floor = ?", "mm_zero = ?"])
            update_values.extend([mm_bar_value, mm_floor_value, mm_zero_value])
        update_values.append(tournament_id)
        updated = conn.execute(
            f"UPDATE tournaments SET {', '.join(update_fields)} WHERE id = ?",
            update_values,
        ).rowcount
        if not updated:
            flash(TRANSLATIONS[lang]["error"])
        else:
            has_matches = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'matches'"
            ).fetchone() is not None
            old_match_dates = []
            if has_matches:
                old_match_dates = conn.execute(
                    """
                    SELECT m.match_date
                    FROM matches m
                    JOIN tournament_pairings p ON p.id = m.tournament_pairing_id
                    JOIN tournament_rounds r ON r.id = p.round_id
                    WHERE r.tournament_id = ?
                    """,
                    (tournament_id,),
                ).fetchall()
            update_tournament_handicaps(
                conn,
                tournament_id,
                handicap_enabled,
                apply_auto_handicap=apply_auto_handicap,
            )
            updated_matches = sync_tournament_matches(
                conn,
                tournament_id,
                name=name,
                match_date=begin_date,
            )
            if pairing_system == "mcmahon":
                participant_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(tournament_participants)").fetchall()
                }
                if "mc_seeds_calculated" in participant_columns:
                    conn.execute(
                        "UPDATE tournament_participants SET mc_seeds_calculated = 0 WHERE tournament_id = ?",
                        (tournament_id,),
                    )
                _recalculate_mcmahon_seeds(conn, tournament_id)
            conn.commit()
            if updated_matches and begin_date:
                for row in old_match_dates:
                    mark_dirty(row["match_date"])
                mark_dirty(begin_date)
                refresh_stats()
            log_admin_action(
                "tournament_settings_updated",
                "tournament",
                {"tournament_id": tournament_id, "name": name, "rounds": rounds},
                user_id=session.get("user_id"),
            )
            flash(TRANSLATIONS[lang]["success"])
    finally:
        conn.close()
    return redirect_or_json(url_for("admin_tournament_settings", tournament_id=tournament_id, lang=lang))

@admin_bp.route("/admin/tournaments/<int:tournament_id>/settings")
def admin_tournament_settings(tournament_id):
    if not admin_required():
        return redirect(url_for("admin_login", lang=get_language(request.args.get("lang"))))
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    tournament = conn.execute(
        "SELECT * FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()
    conn.close()
    if tournament is None:
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_tournaments", lang=lang))
    return render_template(
        "admin/tournament_settings.html",
        tournament=tournament,
        acceleration_categories=acceleration_category_settings(
            tournament["acceleration_scheme"] if "acceleration_scheme" in tournament.keys() else None
        ),
        acceleration_scheme_options=ACCELERATION_SCHEMES,
        acceleration_scheme_choice=acceleration_scheme_choice(
            tournament["acceleration_scheme"] if "acceleration_scheme" in tournament.keys() else None
        ),
        acceleration_rounds=(
            tournament["acceleration_rounds"]
            if "acceleration_rounds" in tournament.keys()
            and tournament["acceleration_rounds"] is not None
            else default_acceleration_rounds(tournament["rounds"])
        ),
        category_rounds=(
            tournament["category_rounds"]
            if "category_rounds" in tournament.keys()
            else DEFAULT_CATEGORY_ROUNDS
        ),
        lang=lang,
        translations=TRANSLATIONS[lang],
    )

@admin_bp.route("/admin/tournaments/<int:tournament_id>/pending-player-resolve", methods=["POST"])
def admin_resolve_pending_player(tournament_id):
    if not admin_required():
        return redirect(url_for("admin_login", lang=get_language(request.args.get("lang"))))
    lang = get_language(request.args.get("lang"))
    pending_id = request.form.get("pending_id", type=int)
    resolved_player_id = request.form.get("resolved_player_id", type=int)

    conn = get_db()
    try:
        if pending_id is None:
            raise ValueError("Pending player not found")
        if resolved_player_id is not None:
            player_row = conn.execute(
                "SELECT display_name FROM players WHERE id = ?",
                (resolved_player_id,),
            ).fetchone()
            if player_row is None:
                raise ValueError("Resolved player not found")
            canonical_name = player_row["display_name"]
            updated = conn.execute(
                """
                UPDATE tournament_pending_players
                SET resolved_player_id = ?, display_name = ?
                WHERE tournament_id = ? AND id = ?
                """,
                (resolved_player_id, canonical_name, tournament_id, pending_id),
            ).rowcount
        else:
            updated = conn.execute(
                """
                UPDATE tournament_pending_players
                SET resolved_player_id = ?
                WHERE tournament_id = ? AND id = ?
                """,
                (resolved_player_id, tournament_id, pending_id),
            ).rowcount
        if not updated:
            raise ValueError("Pending player not found")
        materialized = _materialize_pending_players(
            conn,
            tournament_id,
            pending_id=pending_id,
        )
        if not materialized:
            raise ValueError("Pending player could not be materialized")
        conn.commit()
        log_admin_action(
            "pending_player_resolved",
            "tournament_pending_player",
            {"tournament_id": tournament_id, "pending_id": pending_id, "resolved_player_id": resolved_player_id},
            user_id=session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["success"])
    except (ValueError, sqlite3.DatabaseError) as exc:
        conn.rollback()
        logger.warning("Pending player resolution failed: %s", exc)
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect(url_for("admin_tournament", tournament_id=tournament_id, lang=lang))

@admin_bp.route("/admin/tournaments/<int:tournament_id>")
def admin_tournament(tournament_id):
    if not admin_required():
        return redirect(
            url_for("admin_login", lang=get_language(request.args.get("lang")))
        )
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    tournament = conn.execute(
        "SELECT * FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()
    if tournament is None:
        conn.close()
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_tournaments", lang=lang))
    selected_round_id = request.args.get("round_id", type=int)
    if selected_round_id is None:
        selected_round_id = conn.execute(
            "SELECT id FROM tournament_rounds WHERE tournament_id = ? ORDER BY round_number DESC LIMIT 1",
            (tournament_id,),
        ).fetchone()
        selected_round_id = selected_round_id["id"] if selected_round_id else None
    pairings = conn.execute(
        """
         SELECT p.id, r.id AS round_id, r.round_number, r.status AS round_status, p.board_number,
             p.white_player_id, p.black_player_id,
               p.is_bye, p.result,
               COALESCE(white.display_name, p.white_player_name) AS white_name,
               COALESCE(black.display_name, p.black_player_name) AS black_name
        FROM tournament_rounds r
        JOIN tournament_pairings p ON p.round_id = r.id
        LEFT JOIN players white ON white.id = p.white_player_id
        LEFT JOIN players black ON black.id = p.black_player_id
        WHERE r.tournament_id = ?
        ORDER BY r.round_number DESC, p.board_number
        """,
        (tournament_id,),
    ).fetchall()
    selected_pairings = [pairing for pairing in pairings if pairing["round_id"] == selected_round_id]
    round_statuses = {
        row["player_id"]: row["status"]
        for row in conn.execute(
            "SELECT player_id, status FROM tournament_round_players WHERE round_id = ?",
            (selected_round_id,),
        ).fetchall()
    } if selected_round_id else {}
    all_participants = list_tournament_participants(conn, tournament_id)
    participant_count = len(all_participants)
    participants = [row for row in all_participants if not row["is_pending"]]
    available_players = conn.execute(
        """
        SELECT p.id, p.display_name, p.rating
        FROM players p
        WHERE p.active = 1
          AND NOT EXISTS (
              SELECT 1 FROM tournament_participants tp
              WHERE tp.tournament_id = ? AND tp.player_id = p.id
          )
        ORDER BY p.display_name
        """,
        (tournament_id,),
    ).fetchall()
    rounds = conn.execute(
        "SELECT id, round_number FROM tournament_rounds WHERE tournament_id = ? ORDER BY round_number DESC",
        (tournament_id,),
    ).fetchall()
    paired_player_ids = {
        player_id
        for pairing in selected_pairings
        for player_id in (pairing["white_player_id"], pairing["black_player_id"])
        if player_id is not None
    }
    unpaired_players = [
        participant for participant in participants
        if participant["player_id"] not in paired_player_ids
        and participant["player_id"] not in round_statuses
    ]
    absent_players = [
        participant for participant in participants
        if round_statuses.get(participant["player_id"]) == "absent"
    ]
    pending_rows = conn.execute(
        """
        SELECT *
        FROM tournament_pending_players
        WHERE tournament_id = ?
        ORDER BY rank, display_name
        """,
        (tournament_id,),
    ).fetchall()
    suggested_player_ids = {
        row["display_name"]: row["id"]
        for row in available_players
    }
    pending_players = []
    for row in pending_rows:
        pending = dict(row)
        pending["suggested_player_id"] = suggested_player_ids.get(pending.get("suggested_name"))
        pending_players.append(pending)
    standings = get_tournament_standings(conn, tournament_id)
    conn.close()
    return render_template(
        "admin/tournament.html",
        tournament=tournament,
        pairings=pairings,
        selected_pairings=selected_pairings,
        selected_round_id=selected_round_id,
        participant_count=participant_count,
        participants=participants,
        available_players=available_players,
        unpaired_players=unpaired_players,
        absent_players=absent_players,
        round_statuses=round_statuses,
        rounds=rounds,
        standings=standings,
        pending_players=pending_players,
        lang=lang,
        translations=TRANSLATIONS[lang],
    )

@admin_bp.route("/admin/tournaments/<int:tournament_id>/export")
def admin_export_tournament_results(tournament_id):
    if not admin_required():
        return redirect(url_for("admin_login", lang=get_language(request.args.get("lang"))))
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    try:
        xml_text = export_tournament_results(conn, tournament_id)
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
        conn.close()
        return redirect(url_for("admin_tournament", tournament_id=tournament_id, lang=lang))
    conn.close()
    return Response(
        xml_text,
        content_type="application/xml; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=tournament_{tournament_id}.xml"
        },
    )

@admin_bp.route("/admin/tournaments/<int:tournament_id>/players")
def admin_tournament_players(tournament_id):
    if not admin_required():
        return redirect(url_for("admin_login", lang=get_language(request.args.get("lang"))))
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    tournament = conn.execute(
        "SELECT id, name FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()
    if tournament is None:
        conn.close()
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_tournaments", lang=lang))
    participants = list_tournament_participants(conn, tournament_id)
    available_players = conn.execute(
        """
        SELECT p.id, p.display_name, p.rating
        FROM players p
        WHERE p.active = 1
          AND NOT EXISTS (
              SELECT 1 FROM tournament_participants tp
              WHERE tp.tournament_id = ? AND tp.player_id = p.id
          )
        ORDER BY p.display_name
        """,
        (tournament_id,),
    ).fetchall()
    category_config = get_category_config(conn=conn)
    rank_options = []
    for rank_value in range(8, -31, -1):
        glicko = round(
            category_config["glicko_m"]
            * math.exp((rank_value + 29) / category_config["glicko_k"])
        )
        rank_options.append(
            {
                "label": f"{rank_value + 1} dan" if rank_value >= 0 else f"{-rank_value} kyu",
                "glicko": glicko,
            }
        )
    conn.close()
    return render_template(
        "admin/tournament_players.html",
        tournament=tournament,
        participants=participants,
        available_players=available_players,
        rank_options=rank_options,
        lang=lang,
        translations=TRANSLATIONS[lang],
    )

@admin_bp.route("/admin/tournaments/<int:tournament_id>/participants/add", methods=["POST"])
def admin_add_tournament_participant(tournament_id):
    if not admin_required():
        return redirect(url_for("admin_login", lang=get_language(request.args.get("lang"))))
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    try:
        player_id = request.form.get("player_id", type=int)
        add_participant(conn, tournament_id, player_id)
        log_admin_action(
            "tournament_participant_added",
            "tournament",
            {"tournament_id": tournament_id, "player_id": player_id},
            user_id=session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["success"])
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect(url_for("admin_tournament_players", tournament_id=tournament_id, lang=lang))

@admin_bp.route("/admin/tournaments/<int:tournament_id>/players/create", methods=["POST"])
def admin_create_tournament_player(tournament_id):
    if not admin_required():
        return redirect(url_for("admin_login", lang=get_language(request.args.get("lang"))))
    lang = get_language(request.args.get("lang"))
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    if not first_name or not last_name:
        flash(f"{TRANSLATIONS[lang]['error']}: {TRANSLATIONS[lang]['player_name_required']}")
        return redirect(url_for("admin_tournament_players", tournament_id=tournament_id, lang=lang))

    display_name = f"{first_name} {last_name}"
    selected_glicko = request.form.get("glicko", "").strip()
    category_config = get_category_config()
    valid_glickos = set()
    for rank_value in range(8, -31, -1):
        valid_glickos.add(
            round(
                category_config["glicko_m"]
                * math.exp((rank_value + 29) / category_config["glicko_k"])
            )
        )
    try:
        if not selected_glicko or float(selected_glicko) not in valid_glickos:
            raise ValueError
        glicko = float(selected_glicko)
    except ValueError:
        flash(f"{TRANSLATIONS[lang]['error']}: {TRANSLATIONS[lang]['invalid_rating']}")
        return redirect(url_for("admin_tournament_players", tournament_id=tournament_id, lang=lang))

    conn = get_db()
    try:
        existing_player = _player_lookup(conn).get(normalize_key(display_name))
        if existing_player is not None:
            raise ValueError(
                TRANSLATIONS[lang]["player_already_exists"].format(
                    name=existing_player["display_name"]
                )
            )
        similar_player = _suggest_player_name(display_name, conn)
        if similar_player:
            raise ValueError(
                TRANSLATIONS[lang]["similar_player_exists"].format(
                    name=similar_player
                )
            )
        if conn.execute("SELECT 1 FROM tournaments WHERE id = ?", (tournament_id,)).fetchone() is None:
            raise ValueError("Tournament not found")
        rank = conn.execute(
            "SELECT COALESCE(MAX(rank), 0) + 1 FROM tournament_pending_players WHERE tournament_id = ?",
            (tournament_id,),
        ).fetchone()[0]
        pending_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(tournament_pending_players)").fetchall()
        }
        pending_values = (tournament_id, display_name, glicko, rank, f"manual:{first_name}:{last_name}")
        if "created_at" in pending_columns:
            conn.execute(
                """
                INSERT INTO tournament_pending_players
                    (tournament_id, display_name, rating, rank, source_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                pending_values + (current_timestamp(),),
            )
        else:
            conn.execute(
                """
                INSERT INTO tournament_pending_players
                    (tournament_id, display_name, rating, rank, source_key)
                VALUES (?, ?, ?, ?, ?)
                """,
                pending_values,
            )
        conn.commit()
        log_admin_action(
            "tournament_pending_player_created",
            "tournament_pending_player",
            {"tournament_id": tournament_id, "display_name": display_name, "glicko": glicko},
            user_id=session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["pending_player_created"])
    except (ValueError, sqlite3.IntegrityError) as exc:
        conn.rollback()
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect(url_for("admin_tournament_players", tournament_id=tournament_id, lang=lang))

@admin_bp.route("/admin/tournaments/<int:tournament_id>/pending-player/delete", methods=["POST"])
def admin_delete_pending_player(tournament_id):
    if not admin_required():
        return redirect(url_for("admin_login", lang=get_language(request.args.get("lang"))))
    lang = get_language(request.args.get("lang"))
    pending_id = request.form.get("pending_id", type=int)
    conn = get_db()
    try:
        deleted = conn.execute(
            "DELETE FROM tournament_pending_players WHERE tournament_id = ? AND id = ?",
            (tournament_id, pending_id),
        ).rowcount
        if not deleted:
            raise ValueError("Pending player not found")
        conn.commit()
        log_admin_action(
            "tournament_pending_player_deleted",
            "tournament_pending_player",
            {"tournament_id": tournament_id, "pending_id": pending_id},
            user_id=session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["pending_player_deleted"])
    except (ValueError, sqlite3.DatabaseError) as exc:
        conn.rollback()
        logger.warning("Pending player deletion failed: %s", exc)
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    destination = "admin_tournament_players" if request.form.get("return_to") == "players" else "admin_tournament"
    return redirect(url_for(destination, tournament_id=tournament_id, lang=lang))

@admin_bp.route("/admin/tournaments/<int:tournament_id>/round-status", methods=["POST"])
def admin_set_round_player_status(tournament_id):
    if not admin_required():
        return redirect(url_for("admin_login", lang=get_language(request.args.get("lang"))))
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    try:
        round_id = request.form.get("round_id", type=int)
        player_id = request.form.get("player_id", type=int)
        status = request.form.get("status", "")
        set_round_player_status(
            conn,
            tournament_id,
            round_id,
            player_id,
            status,
        )
        log_admin_action(
            "tournament_player_status_updated",
            "tournament_round_player",
            {"tournament_id": tournament_id, "round_id": round_id, "player_id": player_id, "status": status},
            user_id=session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["success"])
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect_or_json(
        url_for(
            "admin_tournament",
            tournament_id=tournament_id,
            lang=lang,
            round_id=request.form.get("round_id", type=int),
        )
    )

@admin_bp.route("/admin/tournaments/<int:tournament_id>/participants/remove", methods=["POST"])
def admin_remove_tournament_participant(tournament_id):
    if not admin_required():
        return redirect(url_for("admin_login", lang=get_language(request.args.get("lang"))))
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    try:
        player_id = request.form.get("player_id", type=int)
        remove_participant(conn, tournament_id, player_id)
        log_admin_action(
            "tournament_participant_removed",
            "tournament",
            {"tournament_id": tournament_id, "player_id": player_id},
            user_id=session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["success"])
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect(url_for("admin_tournament", tournament_id=tournament_id, lang=lang, round_id=request.form.get("round_id", type=int)))

@admin_bp.route("/admin/tournaments/<int:tournament_id>/pairing-handicap", methods=["POST"])
def admin_update_pairing_handicap(tournament_id):
    """Lets a tournament director override the auto-suggested handicap on
    an already-created pairing (e.g. one generated by generate_next_round
    or pair_selected_players), before results are entered.
    """
    if not admin_required():
        return redirect(url_for("admin_login", lang=get_language(request.args.get("lang"))))
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    try:
        pairing_id = request.form.get("pairing_id", type=int)
        handicap_stones = request.form.get("handicap_stones", type=int)
        update_pairing_handicap(conn, tournament_id, pairing_id, handicap_stones)
        log_admin_action(
            "tournament_pairing_handicap_updated",
            "tournament_pairing",
            {"tournament_id": tournament_id, "pairing_id": pairing_id, "handicap_stones": handicap_stones},
            user_id=session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["success"])
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect_or_json(
        url_for(
            "admin_tournament",
            tournament_id=tournament_id,
            lang=lang,
            round_id=request.form.get("round_id", type=int),
        )
    )

@admin_bp.route("/admin/tournaments/<int:tournament_id>/pair", methods=["POST"])
def admin_manual_pair(tournament_id):
    if not admin_required():
        return redirect(url_for("admin_login", lang=get_language(request.args.get("lang"))))
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    try:
        round_id = request.form.get("round_id", type=int)
        white_player_id = request.form.get("white_player_id", type=int)
        black_player_id = request.form.get("black_player_id", type=int)
        # Blank/absent handicap_stones means "let manual_pair auto-suggest
        # from ratings"; an explicit value (including 0) overrides it.
        raw_handicap = request.form.get("handicap_stones", "").strip()
        handicap_stones = int(raw_handicap) if raw_handicap else None
        manual_pair(
            conn,
            tournament_id,
            round_id,
            white_player_id,
            black_player_id,
            handicap_stones=handicap_stones,
        )
        log_admin_action(
            "tournament_pairing_created",
            "tournament_pairing",
            {"tournament_id": tournament_id, "round_id": round_id, "white_player_id": white_player_id, "black_player_id": black_player_id},
            user_id=session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["success"])
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect(url_for("admin_tournament", tournament_id=tournament_id, lang=lang, round_id=request.form.get("round_id", type=int)))

@admin_bp.route("/admin/tournaments/<int:tournament_id>/pair-selected", methods=["POST"])
def admin_pair_selected_players(tournament_id):
    if not admin_required():
        return redirect(url_for("admin_login", lang=get_language(request.args.get("lang"))))
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    try:
        round_id = request.form.get("round_id", type=int)
        player_ids = request.form.getlist("player_ids")
        pair_selected_players(
            conn,
            tournament_id,
            round_id,
            player_ids,
        )
        log_admin_action(
            "tournament_pairings_created",
            "tournament_round",
            {"tournament_id": tournament_id, "round_id": round_id, "player_count": len(player_ids)},
            user_id=session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["success"])
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect_or_json(url_for("admin_tournament", tournament_id=tournament_id, lang=lang, round_id=request.form.get("round_id", type=int)))

@admin_bp.route("/admin/tournaments/<int:tournament_id>/pairing-edit", methods=["POST"])
def admin_edit_pairing(tournament_id):
    if not admin_required():
        return redirect(url_for("admin_login", lang=get_language(request.args.get("lang"))))
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    pairing_id = request.form.get("pairing_id", type=int)
    round_id = request.form.get("round_id", type=int)
    previous_dates = conn.execute(
        "SELECT match_date FROM matches WHERE tournament_pairing_id = ?",
        (pairing_id,),
    ).fetchall()
    try:
        update_pairing(
            conn,
            tournament_id,
            pairing_id,
            request.form.get("white_player_id", type=int),
            request.form.get("black_player_id", type=int),
        )
        log_admin_action(
            "tournament_pairing_updated",
            "tournament_pairing",
            {"tournament_id": tournament_id, "pairing_id": pairing_id},
            user_id=session.get("user_id"),
        )
        refresh_stats()
        current_dates = conn.execute(
            "SELECT match_date FROM matches WHERE tournament_pairing_id = ?",
            (pairing_id,),
        ).fetchall()
        for row in previous_dates + current_dates:
            mark_dirty(row["match_date"])
        update_from_latest_snapshot()
        flash(TRANSLATIONS[lang]["success"])
    except ValueError as exc:
        conn.rollback()
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect_or_json(
        url_for("admin_tournament", tournament_id=tournament_id, lang=lang, round_id=round_id)
    )

@admin_bp.route("/admin/tournaments/<int:tournament_id>/unpair", methods=["POST"])
def admin_unpair(tournament_id):
    if not admin_required():
        return redirect(url_for("admin_login", lang=get_language(request.args.get("lang"))))
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    pairing_id = request.form.get("pairing_id", type=int)
    previous_dates = conn.execute(
        "SELECT match_date FROM matches WHERE tournament_pairing_id = ?",
        (pairing_id,),
    ).fetchall()
    try:
        unpair(conn, tournament_id, pairing_id)
        log_admin_action(
            "tournament_pairing_removed",
            "tournament_pairing",
            {"tournament_id": tournament_id, "pairing_id": pairing_id},
            user_id=session.get("user_id"),
        )
        refresh_stats()
        for row in previous_dates:
            mark_dirty(row["match_date"])
        update_from_latest_snapshot()
        flash(TRANSLATIONS[lang]["success"])
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect_or_json(url_for("admin_tournament", tournament_id=tournament_id, lang=lang, round_id=request.form.get("round_id", type=int)))

@admin_bp.route("/admin/tournaments/<int:tournament_id>/result", methods=["POST"])
def admin_set_tournament_result(tournament_id):
    if not admin_required():
        return redirect(url_for("admin_login", lang=get_language(request.args.get("lang"))))
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    pairing_id = request.form.get("pairing_id", type=int)
    previous_dates = conn.execute(
        """
        SELECT m.match_date
        FROM matches m
        WHERE m.tournament_pairing_id = ?
        """,
        (pairing_id,),
    ).fetchall()
    try:
        set_pairing_result(
            conn,
            tournament_id,
            pairing_id,
            request.form.get("result", ""),
        )
        current_dates = conn.execute(
            "SELECT match_date FROM matches WHERE tournament_pairing_id = ?",
            (pairing_id,),
        ).fetchall()
        refresh_stats()
        for row in previous_dates + current_dates:
            mark_dirty(row["match_date"])
        update_from_latest_snapshot()
        log_admin_action(
            "tournament_result_updated",
            "tournament_pairing",
            {"tournament_id": tournament_id, "pairing_id": pairing_id},
            user_id=session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["success"])
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect_or_json(url_for("admin_tournament", tournament_id=tournament_id, lang=lang, round_id=request.form.get("round_id", type=int)))

@admin_bp.route("/admin/tournaments/<int:tournament_id>/generate", methods=["POST"])
def admin_generate_tournament_round(tournament_id):
    if not admin_required():
        return redirect(
            url_for("admin_login", lang=get_language(request.args.get("lang")))
        )
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    try:
        generate_next_round(conn, tournament_id)
        log_admin_action(
            "tournament_round_generated",
            "tournament",
            {"tournament_id": tournament_id},
            user_id=session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["success"])
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect_or_json(url_for("admin_tournament", tournament_id=tournament_id, lang=lang))

@admin_bp.route("/admin/tournaments/<int:tournament_id>/save", methods=["POST"])
def admin_save_tournament(tournament_id):
    if not admin_required():
        return redirect(
            url_for("admin_login", lang=get_language(request.args.get("lang")))
        )
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    try:
        saved = save_tournament_matches(conn, tournament_id)
        conn.commit()
        if saved:
            refresh_stats()
            earliest = conn.execute(
                """
                SELECT MIN(m.match_date)
                FROM matches m
                JOIN tournament_pairings p ON p.id = m.tournament_pairing_id
                JOIN tournament_rounds r ON r.id = p.round_id
                WHERE r.tournament_id = ?
                """,
                (tournament_id,),
            ).fetchone()[0]
            if earliest:
                mark_dirty(earliest)
                update_from_latest_snapshot()
        log_admin_action(
            "tournament_saved",
            "tournament",
            {"tournament_id": tournament_id, "matches_saved": saved},
            user_id=session.get("user_id"),
        )
        flash(f"{TRANSLATIONS[lang]['success']} ({saved} matches)")
    except ValueError as exc:
        conn.rollback()
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        logger.exception("Tournament save failed for %s", tournament_id)
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect_or_json(url_for("admin_tournament", tournament_id=tournament_id, lang=lang))

@admin_bp.route("/admin/tournaments/<int:tournament_id>/process-round", methods=["POST"])
def admin_process_tournament_round(tournament_id):
    if not admin_required():
        return redirect(
            url_for("admin_login", lang=get_language(request.args.get("lang")))
        )
    lang = get_language(request.args.get("lang"))
    conn = get_db()
    round_id = request.form.get("round_id", type=int)
    match_date = request.form.get("match_date", "").strip() or None
    event = request.form.get("event", "").strip() or None
    try:
        inserted = process_tournament_round_matches(
            conn,
            tournament_id,
            round_id=round_id,
            match_date=match_date,
            event=event,
        )
        conn.commit()
        log_admin_action(
            "tournament_round_processed",
            "tournament_round",
            {"tournament_id": tournament_id, "round_id": round_id, "matches": inserted},
            user_id=session.get("user_id"),
        )
        if inserted:
            refresh_stats(conn)
            earliest = conn.execute(
                """
                SELECT MIN(match_date)
                FROM matches
                WHERE white_player_id IN (
                    SELECT white_player_id
                    FROM tournament_pairings
                    WHERE round_id = ?
                )
                OR black_player_id IN (
                    SELECT black_player_id
                    FROM tournament_pairings
                    WHERE round_id = ?
                )
                """,
                (round_id, round_id),
            ).fetchone()[0]

            if earliest:
                mark_dirty(earliest)
                update_from_latest_snapshot()
        flash(f"{TRANSLATIONS[lang]['success']} ({inserted} matches)")
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        flash(f"DatabaseError: {exc}")
    finally:
        conn.close()
    return redirect_or_json(
        url_for(
            "admin_tournament",
            tournament_id=tournament_id,
            lang=lang,
            round_id=round_id,
        )
    )

@admin_bp.route("/admin/matches/add", methods=["GET", "POST"])
def admin_add_match():
    if not admin_required():
        return redirect(
            url_for("admin_login", lang=get_language(request.args.get("lang")))
        )

    lang = get_language(request.args.get("lang"))
    conn = get_db()

    if request.method == "POST":
        match_date = request.form.get("match_date", "").strip()
        white_player_id = request.form.get("white_player_id")
        black_player_id = request.form.get("black_player_id")
        result = request.form.get("result", "").strip()
        event = request.form.get("event", "").strip()
        raw_notes = request.form.get("notes", "")
        notes = normalize_round_note_for_storage(raw_notes)

        valid, message = validate_match_form_data(
            conn,
            match_date,
            white_player_id,
            black_player_id,
            result,
            lang,
        )
        handicap_stones = None
        if valid:
            try:
                handicap_stones = parse_handicap_stones(request.form.get("handicap_stones"))
            except ValueError:
                valid = False
                message = f"{TRANSLATIONS[lang]['error']}: {TRANSLATIONS[lang].get('invalid_handicap', 'Invalid handicap')}"

        if not valid:
            flash(message or TRANSLATIONS[lang]["error"])
        elif white_player_id is not None and black_player_id is not None and match_date is not None:
            w_id = int(white_player_id)
            b_id = int(black_player_id)
            conn.execute(
                """
                INSERT INTO matches
                    (match_date, white_player_id, black_player_id, result, event, notes, round_number, handicap_stones)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (match_date.strip(), w_id, b_id, result, event, notes, normalize_round_note(raw_notes), handicap_stones),
            )
            conn.commit()
            refresh_stats()
            mark_dirty(match_date.strip())
            update_from_latest_snapshot()
            flash(TRANSLATIONS[lang]["success"])
            log_admin_action(
                "match_created",
                "match",
                {"match_id": conn.execute("SELECT last_insert_rowid()").fetchone()[0], "event": event or None},
                user_id=session.get("user_id"),
            )
            conn.close()
            return redirect(url_for("admin_matches", lang=lang))

    players = conn.execute(
        "SELECT id, display_name FROM players ORDER BY display_name"
    ).fetchall()
    conn.close()

    return render_template(
        "admin/match_form.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        players=players,
        match=None,
        form_action=url_for("admin_add_match", lang=lang),
    )

@admin_bp.route("/admin/matches/edit", methods=["GET", "POST"])
def admin_edit_match():
    if not admin_required():
        return redirect(
            url_for("admin_login", lang=get_language(request.args.get("lang")))
        )

    lang = get_language(request.args.get("lang"))
    match_id = request.args.get("id")

    if not match_id:
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_matches", lang=lang))

    conn = get_db()

    match = conn.execute(
        "SELECT * FROM matches WHERE id = ?",
        (match_id,),
    ).fetchone()

    if not match:
        conn.close()
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_matches", lang=lang))

    if request.method == "POST":
        match_date = request.form.get("match_date", "").strip()
        white_player_id = request.form.get("white_player_id")
        black_player_id = request.form.get("black_player_id")
        result = request.form.get("result", "").strip()
        event = request.form.get("event", "").strip()
        raw_notes = request.form.get("notes", "")
        notes = normalize_round_note_for_storage(raw_notes)

        valid, message = validate_match_form_data(
            conn,
            match_date,
            white_player_id,
            black_player_id,
            result,
            lang,
        )
        handicap_stones = None
        if valid:
            try:
                handicap_stones = parse_handicap_stones(request.form.get("handicap_stones"))
            except ValueError:
                valid = False
                message = f"{TRANSLATIONS[lang]['error']}: {TRANSLATIONS[lang].get('invalid_handicap', 'Invalid handicap')}"

        if not valid:
            flash(message or TRANSLATIONS[lang]["error"])
        elif white_player_id is not None and black_player_id is not None and match_date is not None:
            w_id = int(white_player_id)
            b_id = int(black_player_id)
            try:
                conn.execute(
                    """
                    UPDATE matches
                    SET match_date = ?, white_player_id = ?, black_player_id = ?,
                        result = ?, event = ?, notes = ?, round_number = ?, handicap_stones = ?
                    WHERE id = ?
                    """,
                    (match_date.strip(), w_id, b_id, result, event, notes, normalize_round_note(raw_notes), handicap_stones, match_id),
                )
                sync_match_pairing(
                    conn,
                    match_id,
                    w_id,
                    b_id,
                    result,
                    handicap_stones,
                )
                conn.commit()
                refresh_stats()
                mark_dirty(match["match_date"])
                mark_dirty(match_date.strip())
                update_from_latest_snapshot()
                flash(TRANSLATIONS[lang]["success"])
                log_admin_action(
                    "match_updated",
                    "match",
                    {"match_id": match_id},
                    user_id=session.get("user_id"),
                )
            except ValueError as exc:
                conn.rollback()
                flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
            except sqlite3.DatabaseError as exc:
                logger.exception("Match %s was saved, but post-save statistics refresh failed; restoring backup.", match_id)
                backup_path = get_latest_valid_backup_path()
                if backup_path is not None and restore_db_from_backup(backup_path):
                    flash("Database recovered from backup after a malformed SQLite image. Please review the restored data before continuing.")
                else:
                    flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
            finally:
                conn.close()
            return redirect(url_for("admin_matches", lang=lang))

    players = conn.execute(
        "SELECT id, display_name FROM players ORDER BY display_name"
    ).fetchall()
    conn.close()

    return render_template(
        "admin/match_form.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        players=players,
        match=match,
        form_action=url_for("admin_edit_match", id=match_id, lang=lang),
    )

@admin_bp.route("/admin/matches/delete", methods=["POST"])
def admin_delete_match():
    if not admin_required():
        return redirect(
            url_for("admin_login", lang=get_language(request.args.get("lang")))
        )

    lang = get_language(request.args.get("lang"))
    match_id = request.args.get("id")

    if not match_id:
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_matches", lang=lang))

    conn = get_db()
    match_row = conn.execute("SELECT match_date FROM matches WHERE id = ?", (match_id,)).fetchone()
    if not match_row:
        conn.close()
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_matches", lang=lang))
    match_date = match_row["match_date"]
    conn.execute("DELETE FROM matches WHERE id = ?", (match_id,))
    conn.commit()
    conn.close()
    log_admin_action(
        "match_deleted",
        "match",
        {"match_id": match_id},
        user_id=session.get("user_id"),
    )

    try:
        refresh_stats()
        mark_dirty(match_date)
        update_from_latest_snapshot()
    except sqlite3.DatabaseError as exc:
        logger.exception("Match deletion for %s left the database in a malformed state; restoring a valid backup.", match_id)
        backup_path = get_latest_valid_backup_path()
        if backup_path is not None and restore_db_from_backup(backup_path):
            flash("Database recovered from backup after a malformed SQLite image. Please review the restored data before continuing.")
        else:
            flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
        return redirect(url_for("admin_matches", lang=lang))

    flash(TRANSLATIONS[lang]["success"])
    return redirect(url_for("admin_matches", lang=lang))

@admin_bp.route("/admin/players")
def admin_players():

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
    total_count = count_rankings({
        "display_name": filters["display_name"],
        "glicko_min": filters["glicko_min"],
        "glicko_max": filters["glicko_max"],
        "last_active": filters["last_active"],
    })
    page_details = pagination_details(total_count, page, page_size)
    page = page_details["page"]
    page_size = page_details["page_size"]

    return render_template(
        "admin/players.html",
        rankings=load_rankings(filters),
        lang=lang,
        translations=TRANSLATIONS[lang],
        total_count=total_count,
        sort=sort_key,
        order=sort_order,
        category_config=category_config,
        **page_details,
    )

@admin_bp.route("/admin/players/edit", methods=["GET", "POST"])
def admin_edit_player():

    if not admin_required("data_admin"):
        return redirect(
            url_for(
                "admin_login",
                lang=get_language(request.args.get("lang"))
            )
        )

    lang = get_language(request.args.get("lang"))
    player_id = request.args.get("id")

    if not player_id:
        flash(TRANSLATIONS[lang]["error"])
        return redirect(
            url_for(
                "admin_players",
                lang=lang
            )
        )

    conn = get_db()

    player = conn.execute(
        "SELECT * FROM players WHERE id = ?",
        (player_id,)
    ).fetchone()

    if not player:
        conn.close()

        flash(TRANSLATIONS[lang]["error"])

        return redirect(
            url_for(
                "admin_players",
                lang=lang
            )
        )

    if request.method == "POST":

        display_name = request.form.get(
            "display_name",
            ""
        ).strip()

        first_name = request.form.get(
            "first_name",
            ""
        ).strip()

        last_name = request.form.get(
            "last_name",
            ""
        ).strip()

        slug = request.form.get(
            "slug",
            ""
        ).strip()

        initial_rating = request.form.get("initial_rating")
        country = request.form.get("country")
        club = request.form.get("club")
        active = int(request.form.get("active", 1))            
        initial_rating_changed = initial_rating != (
            "" if player["initial_rating"] is None else str(player["initial_rating"])
        )

        conn.execute(
            """
            UPDATE players
            SET
                display_name = ?,
                first_name = ?,
                last_name = ?,
                slug = ?,
                initial_rating = ?,
                country = ?,
                club = ?,
                active = ?
            WHERE id = ?
            """,
            (
                display_name,
                first_name,
                last_name,
                slug,
                initial_rating,
                country,
                club,
                active,
                player_id,
            ),
        )

        conn.commit()
        earliest_match_date = None
        if initial_rating_changed:
            earliest_match_date = conn.execute(
                """
                SELECT MIN(match_date)
                FROM matches
                WHERE white_player_id = ? OR black_player_id = ?
                """,
                (player_id, player_id),
            ).fetchone()[0]
        conn.close()

        if earliest_match_date:
            mark_dirty(earliest_match_date)
            update_from_latest_snapshot()

        log_admin_action(
            "player_updated",
            "player",
            {"player_id": player_id, "active": active, "initial_rating_changed": initial_rating_changed},
            user_id=session.get("user_id"),
        )

        flash(TRANSLATIONS[lang]["success"])

        return redirect(
            url_for(
                "admin_players",
                lang=lang
            )
        )

    conn.close()

    return render_template(
        "admin/edit_player.html",
        player=player,
        lang=lang,
        translations=TRANSLATIONS[lang],
    )

@admin_bp.route("/admin/players/delete", methods=["POST"])
def admin_delete_player():
    if not admin_required("data_admin"):
        return redirect(
            url_for(
                "admin_login",
                lang=get_language(request.args.get("lang"))
            )
        )
    lang = get_language(request.args.get("lang"))
    player_id = request.args.get("id")

    if not player_id:
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_players", lang=lang))

    conn = get_db()
    try:
        earliest_match_date = conn.execute(
            """
            SELECT MIN(match_date)
            FROM matches
            WHERE white_player_id = ? OR black_player_id = ?
            """,
            (player_id, player_id),
        ).fetchone()[0]
        conn.execute(
            """
            DELETE FROM tournament_pairings
            WHERE white_player_id = ? OR black_player_id = ?
            """,
            (player_id, player_id),
        )
        conn.execute(
            "DELETE FROM tournament_round_players WHERE player_id = ?",
            (player_id,),
        )
        conn.execute(
            "DELETE FROM tournament_participants WHERE player_id = ?",
            (player_id,),
        )
        conn.execute(
            "DELETE FROM rating_snapshots WHERE player_id = ?",
            (player_id,),
        )
        conn.execute(
            """
            DELETE FROM matches
            WHERE white_player_id = ? OR black_player_id = ?
            """,
            (player_id, player_id),
        )
        conn.execute(
            "DELETE FROM players WHERE id = ?",
            (player_id,),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()

    try:
        refresh_stats()
        if earliest_match_date:
            recompute_ratings()
    except sqlite3.DatabaseError as exc:
        logger.exception("Player deletion for %s left the database in a malformed state; restoring a valid backup.", player_id)
        backup_path = get_latest_valid_backup_path()
        if backup_path is not None and restore_db_from_backup(backup_path):
            flash("Database recovered from backup after a malformed SQLite image. Please review the restored data before continuing.")
        else:
            flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
        return redirect(url_for("admin_players", lang=lang))

    flash(TRANSLATIONS[lang]["success"])
    log_admin_action(
        "player_deleted",
        "player",
        {"player_id": player_id},
        user_id=session.get("user_id"),
    )
    return redirect(url_for("admin_players", lang=lang))


@admin_bp.route(
    "/admin/categories",
    methods=["GET", "POST"]
)
def admin_categories():

    if not admin_required("data_admin"):
        return redirect(
            url_for(
                "admin_login",
                lang=get_language(request.args.get("lang"))
            )
        )

    lang = get_language(request.args.get("lang"))

    conn = get_db()

    preview_players = []

    config = get_category_config()

    current_k = config["glicko_k"]
    current_m = config["glicko_m"]

    if request.method == "POST":

        action = request.form.get("action")

        if action == "reset":
            update_category_config(GLICKO_K, GLICKO_M)
            log_admin_action(
                "category_config_reset",
                "category_config",
                {"glicko_k": GLICKO_K, "glicko_m": GLICKO_M},
                user_id=session.get("user_id"),
            )
            flash(TRANSLATIONS[lang]["reset_to_default_success"])
            conn.close()
            return redirect(url_for("admin_categories", lang=lang))

        raw_k = request.form.get("glicko_k", current_k)
        raw_m = request.form.get("glicko_m", current_m)

        try:
            preview_k = float(raw_k)
            preview_m = float(raw_m)
        except (TypeError, ValueError):
            flash(f"{TRANSLATIONS[lang]['error']}: invalid category parameters")
        else:
            if action == "save":
                try:
                    update_category_config(
                        preview_k,
                        preview_m,
                    )
                except ValueError as exc:
                    flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
                else:
                    log_admin_action(
                        "category_config_updated",
                        "category_config",
                        {"glicko_k": preview_k, "glicko_m": preview_m},
                        user_id=session.get("user_id"),
                    )
                    flash(
                        TRANSLATIONS[lang]["success"]
                    )

                    current_k = preview_k
                    current_m = preview_m

            else:
                current_k = preview_k
                current_m = preview_m

    top_players = conn.execute(
        """
        SELECT *
        FROM players
        WHERE games_played > 0
        ORDER BY rating DESC
        LIMIT 5
        """
    ).fetchall()

    random_players = conn.execute(
        """
        SELECT *
        FROM players
        WHERE active = 1
        ORDER BY RANDOM()
        LIMIT 5
        """
    ).fetchall()

    seen = set()

    for player in list(top_players) + list(random_players):

        if player["id"] in seen:
            continue

        seen.add(player["id"])

        current_category = glicko_to_category(
            player["rating"],
            1
        )

        preview_category = glicko_to_category(
            player["rating"],
            1,
            k=current_k,
            m=int(current_m)
        )

        preview_players.append(
            {
                "id": player["id"],
                "display_name": player["display_name"],
                "rating": player["rating"],
                "current_category": current_category,
                "preview_category": preview_category,
                "changed": (
                    current_category
                    != preview_category
                ),
            }
        )

    conn.close()

    return render_template(
        "admin/categories.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        glicko_k=current_k,
        glicko_m=current_m,
        preview_players=preview_players,
    )



@admin_bp.route(
    "/admin/ratings",
    methods=["GET", "POST"]
)
def admin_ratings():

    if not admin_required("data_admin"):
        return redirect(
            url_for(
                "admin_login",
                lang=get_language(request.args.get("lang"))
            )
        )
    cfg = get_rating_config()

    lang = get_language(request.args.get("lang"))

    if request.method == "POST":

        action = request.form.get("action")

        if action == "reset":
            payload = {
                "tau": TAU,
                "default_rating": DEFAULT_RATING,
                "default_rd": DEFAULT_RD,
                "default_volatility": DEFAULT_VOLATILITY,
            }
            update_rating_config(**payload)
            log_admin_action(
                "rating_config_reset",
                "rating_config",
                payload,
                user_id=session.get("user_id"),
            )
            flash(TRANSLATIONS[lang]["reset_to_default_success"])
        elif action == "save":

            payload = {
                "tau": float(request.form["tau"]),
                "default_rating": float(request.form["default_rating"]),
                "default_rd": float(request.form["default_rd"]),
                "default_volatility": float(request.form["default_volatility"]),
            }
            update_rating_config(
                payload["tau"],
                payload["default_rating"],
                payload["default_rd"],
                payload["default_volatility"],
            )
            log_admin_action(
                "rating_config_updated",
                "rating_config",
                payload,
                user_id=session.get("user_id"),
            )

            flash(TRANSLATIONS[lang]["success"])

        elif action == "recalculate":

            try:
                recompute_ratings()
                refresh_stats()
                log_admin_action(
                    "ratings_recalculated",
                    "ratings",
                    {},
                    user_id=session.get("user_id"),
                )
                flash(TRANSLATIONS[lang]["success"])
            except sqlite3.DatabaseError as exc:
                logger.exception(
                    "Ratings recomputation failed because the SQLite database is malformed. "
                    "Restoring the newest valid backup."
                )
                backup_path = get_latest_valid_backup_path()
                if backup_path is not None and restore_db_from_backup(backup_path):
                    restored_conn = sqlite3.connect(DB_PATH)
                    try:
                        refresh_stats(conn=restored_conn)
                    finally:
                        restored_conn.close()
                    flash(
                        "Database recovered from backup after a malformed SQLite image. "
                        "Please review the restored data before continuing."
                    )
                else:
                    flash(f"{TRANSLATIONS[lang]['error']}: {exc}")

        #
        elif action == "update":

            update_from_latest_snapshot()
            refresh_stats()

            flash(
                TRANSLATIONS[lang]["success"]
            )
        
        return redirect(
            url_for(
                "admin_ratings",
                lang=lang
            )
        )

    conn = get_db()

    player_count = conn.execute(
        "SELECT COUNT(*) FROM players"
    ).fetchone()[0]

    match_count = conn.execute(
        "SELECT COUNT(*) FROM matches"
    ).fetchone()[0]

    snapshot_count = conn.execute(
        "SELECT COUNT(*) FROM rating_snapshots"
    ).fetchone()[0]

    conn.close()

    dirty_date = get_dirty_date()

    return render_template(
        "admin/ratings.html",
        config=get_rating_config(),
        dirty_date=dirty_date,
        player_count=player_count,
        match_count=match_count,
        snapshot_count=snapshot_count,
        lang=lang,
        translations=TRANSLATIONS[lang],
    )

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


@admin_bp.route("/admin/audit")
def admin_audit_review():
    permission_error = require_permission("admin")
    if permission_error is not None:
        return permission_error

    lang = get_language(request.args.get("lang"))
    selected_user_id = (request.args.get("user_id") or "").strip()
    selected_action_type = (request.args.get("action_type") or "").strip()
    search_text = (request.args.get("q") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    for date_value_name in ("date_from", "date_to"):
        date_value = locals()[date_value_name]
        if date_value:
            try:
                datetime.strptime(date_value, "%Y-%m-%d")
            except ValueError:
                if date_value_name == "date_from":
                    date_from = ""
                else:
                    date_to = ""

    conn = get_db()
    try:
        users = conn.execute(
            "SELECT id, username FROM users ORDER BY username"
        ).fetchall()
        action_types = [
            row["action_type"]
            for row in conn.execute(
                "SELECT DISTINCT action_type FROM audit_log ORDER BY action_type"
            ).fetchall()
        ]

        query = """
            SELECT a.id, a.user_id, a.action_type, a.resource_type, a.details, a.created_at,
                   COALESCE(u.username, 'System') AS username
            FROM audit_log a
            LEFT JOIN users u ON u.id = a.user_id
            WHERE 1 = 1
        """
        params = []

        if selected_user_id not in ("", "all"):
            if selected_user_id.isdigit():
                query += " AND a.user_id = ?"
                params.append(int(selected_user_id))
            else:
                selected_user_id = ""

        if selected_action_type:
            query += " AND a.action_type = ?"
            params.append(selected_action_type)

        if search_text:
            query += " AND (u.username LIKE ? OR a.action_type LIKE ? OR a.resource_type LIKE ? OR a.details LIKE ?)"
            search_pattern = f"%{search_text}%"
            params.extend([search_pattern] * 4)
        if date_from:
            query += " AND a.created_at >= ?"
            params.append(f"{date_from} 00:00:00")
        if date_to:
            query += " AND a.created_at <= ?"
            params.append(f"{date_to} 23:59:59")

        query += " ORDER BY a.created_at DESC LIMIT 200"
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    events = []
    for row in rows:
        events.append(
            {
                "id": row["id"],
                "username": row["username"],
                "action_type": row["action_type"],
                "resource_type": row["resource_type"],
                "created_at": row["created_at"],
                "details_summary": _audit_details_summary(row["details"]),
            }
        )

    return render_template(
        "admin/audit.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        events=events,
        users=users,
        action_types=action_types,
        selected_user_id=selected_user_id,
        selected_action_type=selected_action_type,
        search_text=search_text,
        date_from=date_from,
        date_to=date_to,
    )


@admin_bp.route("/admin/users")
def admin_users():
    permission_error = require_permission("admin")
    if permission_error is not None:
        return permission_error

    lang = get_language(request.args.get("lang"))
    conn = get_db()
    try:
        users = conn.execute(
            """
            SELECT u.id,
                   u.username,
                   u.is_active,
                     u.timezone,
                     u.email,
                    u.player_id,
                   COALESCE(GROUP_CONCAT(r.name, ', '), '') AS role_names
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            LEFT JOIN roles r ON r.id = ur.role_id
                 GROUP BY u.id, u.username, u.is_active, u.timezone, u.email, u.player_id
            ORDER BY u.username
            """
        ).fetchall()
    finally:
        conn.close()

    player_names = {
        player["id"]: player["display_name"]
        for player in load_players_for_user_link()
    }
    users = [dict(user, player_name=player_names.get(user["player_id"])) for user in users]

    return render_template(
        "admin/users.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        users=users,
        roles=list(ALLOWED_ROLES),
        timezone_choices=get_timezone_choices(),
        timezone_labels={
            timezone: format_timezone_label(timezone)
            for timezone in get_timezone_choices()
        },
    )


@admin_bp.route("/admin/users/create", methods=["GET", "POST"])
def admin_create_user():
    permission_error = require_permission("admin")
    if permission_error is not None:
        return permission_error

    lang = get_language(request.args.get("lang"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        role_name = (request.form.get("role_name") or "operator").strip()
        timezone_name = (request.form.get("timezone") or "").strip()
        email = (request.form.get("email") or "").strip()
        player_id = request.form.get("player_id") or None
        if player_id is not None:
            try:
                player_id = int(player_id)
            except ValueError:
                player_id = None

        try:
            user_id = create_user_account(
                username,
                password,
                role_name=role_name,
                timezone_name=timezone_name,
                email=email,
                player_id=player_id,
            )
            log_admin_action(
                "user_created",
                "user",
                {"username": username, "role": role_name},
                user_id=session.get("user_id"),
            )
            flash(TRANSLATIONS[lang]["user_created_success"])
            return redirect(url_for("admin_users", lang=lang))
        except ValueError as exc:
            message_key = {
                "unsupported timezone": "invalid_timezone",
                "invalid email": "invalid_email",
                "email already exists": "email_taken",
            }.get(str(exc))
            flash(TRANSLATIONS[lang][message_key] if message_key else TRANSLATIONS[lang]["error"])

    return render_template(
        "admin/create_user.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        roles=list(ALLOWED_ROLES),
        players=load_players_for_user_link(),
        timezone_choices=get_timezone_choices(),
        timezone_labels={
            timezone: format_timezone_label(timezone)
            for timezone in get_timezone_choices()
        },
    )


@admin_bp.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
def admin_edit_user(user_id):
    permission_error = require_permission("admin")
    if permission_error is not None:
        return permission_error

    lang = get_language(request.args.get("lang"))
    conn = get_db()
    user = conn.execute(
        """
        SELECT u.id, u.username, u.is_active, u.timezone, u.email, u.player_id,
               COALESCE(r.name, 'operator') AS role_name
        FROM users u
        LEFT JOIN user_roles ur ON ur.user_id = u.id
        LEFT JOIN roles r ON r.id = ur.role_id
        WHERE u.id = ?
        ORDER BY r.name
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    conn.close()

    if user is None:
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_users", lang=lang))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        role_name = (request.form.get("role_name") or user["role_name"] or "operator").strip()
        timezone_name = (request.form.get("timezone") or "").strip()
        email = (request.form.get("email") or "").strip()
        player_id = request.form.get("player_id") or None
        if player_id is not None:
            try:
                player_id = int(player_id)
            except ValueError:
                player_id = None
        is_active_raw = request.form.get("is_active", "1")
        is_active = 1 if str(is_active_raw).lower() in {"1", "true", "on", "yes"} else 0

        if not username:
            flash(TRANSLATIONS[lang]["user_username_required"])
            return redirect(url_for("admin_edit_user", user_id=user_id, lang=lang))
        try:
            timezone_name = validate_timezone(timezone_name)
            email = validate_email_address(email) if email else None
        except ValueError as exc:
            message_key = "invalid_email" if str(exc) == "invalid email" else "invalid_timezone"
            flash(TRANSLATIONS[lang][message_key])
            return redirect(url_for("admin_edit_user", user_id=user_id, lang=lang))

        conn = get_db()
        try:
            existing = conn.execute(
                "SELECT id FROM users WHERE username = ? AND id != ?",
                (username, user_id),
            ).fetchone()
            if existing is not None:
                flash(TRANSLATIONS[lang]["user_username_taken"])
                return redirect(url_for("admin_edit_user", user_id=user_id, lang=lang))
            existing_email = conn.execute(
                "SELECT id FROM users WHERE email = ? AND id != ?",
                (email, user_id),
            ).fetchone() if email else None
            if existing_email is not None:
                flash(TRANSLATIONS[lang]["email_taken"])
                return redirect(url_for("admin_edit_user", user_id=user_id, lang=lang))

            if player_id is not None and conn.execute("SELECT 1 FROM players WHERE id = ?", (player_id,)).fetchone() is None:
                flash(TRANSLATIONS[lang]["error"])
                return redirect(url_for("admin_edit_user", user_id=user_id, lang=lang))
            conn.execute(
                "UPDATE users SET username = ?, is_active = ?, timezone = ?, email = ?, player_id = ? WHERE id = ?",
                (username, is_active, timezone_name, email, player_id, user_id),
            )
            if password:
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(password), user_id),
                )

            conn.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
            role = conn.execute("SELECT id FROM roles WHERE name = ?", (role_name,)).fetchone()
            if role is not None:
                conn.execute(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
                    (user_id, role["id"]),
                )
            conn.commit()
            log_admin_action(
                "user_updated",
                "user",
                {
                    "target_user_id": user_id,
                    "username": username,
                    "role": role_name,
                    "is_active": is_active,
                    "password_changed": bool(password),
                },
                user_id=session.get("user_id"),
            )
            flash(TRANSLATIONS[lang]["user_updated_success"])
        finally:
            conn.close()
        return redirect(url_for("admin_users", lang=lang))

    return render_template(
        "admin/edit_user.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        user=user,
        roles=list(ALLOWED_ROLES),
        players=load_players_for_user_link(),
        timezone_choices=get_timezone_choices(),
        timezone_labels={
            timezone: format_timezone_label(timezone)
            for timezone in get_timezone_choices()
        },
    )


@admin_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
def admin_delete_user(user_id):
    permission_error = require_permission("admin")
    if permission_error is not None:
        return permission_error

    lang = get_language(request.args.get("lang"))
    conn = get_db()
    try:
        conn.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        log_admin_action(
            "user_deleted",
            "user",
            {"target_user_id": user_id},
            user_id=session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["user_deleted_success"])
    finally:
        conn.close()

    return redirect(url_for("admin_users", lang=lang))


@admin_bp.route("/admin/backups")
def admin_backups():
    permission_error = require_permission("admin")
    if permission_error is not None:
        return permission_error

    lang = get_language(request.args.get("lang"))

    ensure_backup_dir()
    backups = []

    for filename in sorted(
        os.listdir(BACKUP_DIR),
        reverse=True
    ):

        if not filename.endswith(".db"):
            continue

        path = os.path.join(
            BACKUP_DIR,
            filename
        )

        backups.append(
            {
                "name": filename,
                "modified": datetime.fromtimestamp(
                    os.path.getmtime(path), tz=current_datetime().tzinfo
                ).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    return render_template(
        "admin/backups.html",
        backups=backups,
        lang=lang,
        translations=TRANSLATIONS[lang],
    )

#
@admin_bp.route(
    "/admin/backups/create",
    methods=["POST"]
)
def admin_create_backup():

    if not admin_required():
        return redirect(
            url_for(
                "admin_login",
                lang=get_language(request.args.get("lang"))
            )
        )

    lang = get_language(request.args.get("lang"))
    ensure_backup_dir()

    filename = current_datetime().strftime("%Y-%m-%d-%H%M%S") + ".db"

    shutil.copy2(
        DB_PATH,
        os.path.join(
            BACKUP_DIR,
            filename
        )
    )
    log_admin_action(
        "backup_created",
        "backup",
        {"filename": filename},
        user_id=session.get("user_id"),
    )

    flash(
        TRANSLATIONS[lang]["success"]
    )

    return redirect(
        url_for(
            "admin_backups",
            lang=lang
        )
    )
#
@admin_bp.route(
    "/admin/backups/restore",
    methods=["POST"]
)
def admin_restore_backup():

    if not admin_required():
        return redirect(
            url_for(
                "admin_login",
                lang=get_language(request.args.get("lang"))
            )
        )

    lang = get_language(request.args.get("lang"))

    path = get_backup_path(request.form.get("name"))

    if path is None or not path.is_file():
        logger.warning("Backup restore rejected: missing or invalid backup path %r", request.form.get("name"))
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_backups", lang=lang))

    if not is_valid_sqlite_backup(path):
        logger.warning("Backup restore rejected: invalid SQLite backup %s", path)
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_backups", lang=lang))

    if not restore_db_from_backup(path):
        logger.warning("Backup restore failed during restore step for %s", path)
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_backups", lang=lang))

    log_admin_action(
        "backup_restored",
        "backup",
        {"filename": path.name},
        user_id=session.get("user_id"),
    )

    flash(
        TRANSLATIONS[lang]["success"]
    )

    return redirect(
        url_for(
            "admin_backups",
            lang=lang
        )
    )
#
@admin_bp.route(
    "/admin/backups/delete",
    methods=["POST"]
)
def admin_delete_backup():

    if not admin_required():
        return redirect(
            url_for(
                "admin_login",
                lang=get_language(request.args.get("lang"))
            )
        )

    lang = get_language(request.args.get("lang"))

    path = get_backup_path(request.form.get("name"))

    if path is None or not path.is_file():
        flash(
            TRANSLATIONS[lang]["error"]
        )

        return redirect(
            url_for(
                "admin_backups",
                lang=lang
            )
        )

    os.remove(path)
    log_admin_action(
        "backup_deleted",
        "backup",
        {"filename": path.name},
        user_id=session.get("user_id"),
    )

    flash(
        TRANSLATIONS[lang]["success"]
    )

    return redirect(
        url_for(
            "admin_backups",
            lang=lang
        )
    )
#

def register_admin_routes(app):
    if "admin" not in app.blueprints:
        app.register_blueprint(admin_bp)
