"""Display-only player names.

Player names are never stored. A name is rendered from first_name and
last_name with the site-wide format in application_settings, unless the
player has a custom_display_name. The views created here expose the rendered
name as a display_name column, so queries read it without per-row Python.
render_display_name mirrors the SQL exactly; keep the two in sync.

This module must not import app configuration (config, Flask).
"""
import logging
import sqlite3

logger = logging.getLogger(__name__)

DISPLAY_NAME_FORMATS = ("last-first", "first-last", "custom")
DEFAULT_DISPLAY_NAME_FORMAT = "last-first"
DEFAULT_DISPLAY_NAME_TEMPLATE = "{last_name}, {first_name}"
DISPLAY_NAME_TEMPLATE_MAX_LENGTH = 100
DISPLAY_NAME_PLACEHOLDERS = ("{first_name}", "{last_name}")

PLAYER_DISPLAY_VIEW = "players_display"
PENDING_DISPLAY_VIEW = "tournament_pending_players_display"

_FORMAT_SQL = "(SELECT display_name_format FROM application_settings WHERE id = 1)"
_TEMPLATE_SQL = "(SELECT display_name_template FROM application_settings WHERE id = 1)"


def normalize_name_text(value):
    return " ".join(str(value or "").split())


NAME_COMMA_MESSAGE = "Player names cannot contain commas"
NAME_TRIGGER_TABLES = ("players", "tournament_pending_players")


def render_display_name(first_name, last_name, fmt=DEFAULT_DISPLAY_NAME_FORMAT, template=None):
    first = first_name or ""
    last = last_name or ""
    if fmt == "first-last":
        text = f"{first} {last}"
    elif fmt == "custom":
        text = (template or DEFAULT_DISPLAY_NAME_TEMPLATE).replace(
            "{first_name}", first).replace("{last_name}", last)
    else:
        text = f"{last}, {first}"
    return text.strip(" ,")


def split_display_name(display_name):
    """Return (first_name, last_name) from a 'Last, First' or 'First Last' text."""
    text = normalize_name_text(display_name)
    if text.count(",") > 1:
        raise ValueError(NAME_COMMA_MESSAGE)
    if "," in text:
        last_name, first_name = text.split(",", 1)
        return first_name.strip(), last_name.strip()
    parts = text.split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return text, ""


def read_display_name_setting(conn):
    """Return (format, template) from application_settings.

    Read-only, so it is safe inside an import transaction. Falls back to the
    defaults when the settings table or its display columns are missing.
    """
    try:
        row = conn.execute(
            "SELECT display_name_format, display_name_template "
            "FROM application_settings WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is None:
        return DEFAULT_DISPLAY_NAME_FORMAT, DEFAULT_DISPLAY_NAME_TEMPLATE
    return (row[0] or DEFAULT_DISPLAY_NAME_FORMAT,
            row[1] or DEFAULT_DISPLAY_NAME_TEMPLATE)


def display_name_sql(first_expr, last_expr):
    first = f"COALESCE({first_expr}, '')"
    last = f"COALESCE({last_expr}, '')"
    template = (f"COALESCE(NULLIF({_TEMPLATE_SQL}, ''), "
                f"'{DEFAULT_DISPLAY_NAME_TEMPLATE}')")
    custom = (f"REPLACE(REPLACE({template}, '{{first_name}}', {first}), "
              f"'{{last_name}}', {last})")
    return (
        f"TRIM(CASE {_FORMAT_SQL} "
        f"WHEN 'first-last' THEN {first} || ' ' || {last} "
        f"WHEN 'custom' THEN {custom} "
        f"ELSE {last} || ', ' || {first} END, ' ,')"
    )


def create_display_name_views(conn):
    conn.execute(f"DROP VIEW IF EXISTS {PLAYER_DISPLAY_VIEW}")
    conn.execute(f"DROP VIEW IF EXISTS {PENDING_DISPLAY_VIEW}")
    player_name = display_name_sql("p.first_name", "p.last_name")
    conn.execute(
        f"""
        CREATE VIEW {PLAYER_DISPLAY_VIEW} AS
        SELECT p.*,
            CASE WHEN TRIM(COALESCE(p.custom_display_name, '')) <> ''
                 THEN p.custom_display_name
                 ELSE {player_name}
            END AS display_name
        FROM players p
        """
    )
    pending_name = display_name_sql("t.first_name", "t.last_name")
    conn.execute(
        f"""
        CREATE VIEW {PENDING_DISPLAY_VIEW} AS
        SELECT t.*, {pending_name} AS display_name
        FROM tournament_pending_players t
        """
    )


def _table_columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _keep_hand_edited_names(conn, fmt, template):
    """Store names that no standard format would render as custom_display_name."""
    rows = conn.execute(
        "SELECT id, first_name, last_name, display_name, custom_display_name "
        "FROM players"
    ).fetchall()
    for player_id, first, last, stored, custom in rows:
        if custom and custom.strip():
            continue
        text = normalize_name_text(stored)
        if not text:
            continue
        standard = {
            normalize_name_text(render_display_name(first, last, name_format, template))
            for name_format in ("last-first", "first-last", fmt)
        }
        if text not in standard:
            conn.execute(
                "UPDATE players SET custom_display_name = ? WHERE id = ?",
                (text, player_id),
            )


def _split_legacy_display_name(display_name):
    """Split a stored name, dropping extra commas that the strict parser rejects."""
    text = normalize_name_text(display_name)
    if text.count(",") > 1:
        text = normalize_name_text(text.replace(",", " "))
    return split_display_name(text)


def _strip_legacy_name_commas(conn):
    for table in NAME_TRIGGER_TABLES:
        if not {"first_name", "last_name"}.issubset(_table_columns(conn, table)):
            continue
        rows = conn.execute(
            f"SELECT id, first_name, last_name FROM {table} "
            "WHERE instr(first_name, ',') > 0 OR instr(last_name, ',') > 0"
        ).fetchall()
        for row_id, first, last in rows:
            conn.execute(
                f"UPDATE {table} SET first_name = ?, last_name = ? WHERE id = ?",
                (normalize_name_text((first or "").replace(",", " ")),
                 normalize_name_text((last or "").replace(",", " ")), row_id),
            )
        if rows:
            logger.warning("Replaced commas in %d %s name(s)", len(rows), table)


def ensure_name_comma_guards(conn):
    """Reject first or last names containing commas at the database level."""
    condition = "instr(NEW.first_name, ',') > 0 OR instr(NEW.last_name, ',') > 0"
    for table in NAME_TRIGGER_TABLES:
        if not {"first_name", "last_name"}.issubset(_table_columns(conn, table)):
            continue
        for event, suffix in (
            ("BEFORE INSERT", "insert"),
            ("BEFORE UPDATE OF first_name, last_name", "update"),
        ):
            conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS {table}_name_no_comma_{suffix} "
                f"{event} ON {table} WHEN {condition} "
                f"BEGIN SELECT RAISE(ABORT, '{NAME_COMMA_MESSAGE}'); END"
            )


def _split_pending_names(conn):
    rows = conn.execute(
        "SELECT id, display_name FROM tournament_pending_players").fetchall()
    for pending_id, display_name in rows:
        first, last = _split_legacy_display_name(display_name)
        conn.execute(
            "UPDATE tournament_pending_players SET first_name = ?, last_name = ? "
            "WHERE id = ?",
            (first, last, pending_id),
        )


def _backfill_pending_suggestions(conn):
    """Link legacy suggestions whose name matches exactly one active player.

    Runs only in the migration that adds suggested_player_id, so later changes
    to names never link anything automatically.
    """
    rows = conn.execute(
        "SELECT id, suggested_name FROM tournament_pending_players "
        "WHERE TRIM(COALESCE(suggested_name, '')) <> ''").fetchall()
    for pending_id, name in rows:
        matches = conn.execute(
            f"SELECT id FROM {PLAYER_DISPLAY_VIEW} WHERE active = 1 AND display_name = ?",
            (name,),
        ).fetchall()
        if len(matches) == 1:
            conn.execute(
                "UPDATE tournament_pending_players SET suggested_player_id = ? WHERE id = ?",
                (matches[0][0], pending_id),
            )


def migrate_display_name_schema(conn):
    """Upgrade legacy databases to display-only names. Safe to run repeatedly.

    Keeps hand-edited names as custom_display_name, drops the stored
    display_name columns and the FTS objects that reference them, then
    recreates the rendering views.
    """
    conn.execute(f"DROP VIEW IF EXISTS {PLAYER_DISPLAY_VIEW}")
    conn.execute(f"DROP VIEW IF EXISTS {PENDING_DISPLAY_VIEW}")

    added_suggestion_column = False
    player_columns = _table_columns(conn, "players")
    if not player_columns:
        return
    if "custom_display_name" not in player_columns:
        conn.execute("ALTER TABLE players ADD COLUMN custom_display_name TEXT")
    if "display_name" in player_columns:
        fmt, template = read_display_name_setting(conn)
        _keep_hand_edited_names(conn, fmt, template)
        conn.execute("DROP TRIGGER IF EXISTS players_fts_ai")
        conn.execute("DROP TRIGGER IF EXISTS players_fts_ad")
        conn.execute("DROP TRIGGER IF EXISTS players_fts_au")
        conn.execute("DROP TABLE IF EXISTS players_fts")
        conn.execute("DROP INDEX IF EXISTS idx_players_display_name")
        conn.execute("ALTER TABLE players DROP COLUMN display_name")

    pending_columns = _table_columns(conn, "tournament_pending_players")
    if pending_columns:
        for column in ("first_name", "last_name"):
            if column not in pending_columns:
                conn.execute(
                    f"ALTER TABLE tournament_pending_players "
                    f"ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        if "suggested_player_id" not in pending_columns:
            conn.execute(
                "ALTER TABLE tournament_pending_players ADD COLUMN suggested_player_id INTEGER")
            added_suggestion_column = True
        if "display_name" in pending_columns:
            _split_pending_names(conn)
            conn.execute(
                "ALTER TABLE tournament_pending_players DROP COLUMN display_name")

    _strip_legacy_name_commas(conn)
    ensure_name_comma_guards(conn)
    create_display_name_views(conn)
    if added_suggestion_column:
        _backfill_pending_suggestions(conn)
