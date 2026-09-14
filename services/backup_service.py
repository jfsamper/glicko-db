"""Database backup discovery, validation, and restoration helpers."""
import logging
import os
import re
import shutil
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)
BACKUP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.db$")


def ensure_backup_dir(backup_dir):
    os.makedirs(backup_dir, exist_ok=True)


def get_backup_path(filename, backup_dir):
    """Return a safe, server-generated backup path or ``None``."""
    if not isinstance(filename, str):
        logger.warning("Rejected backup path with non-string filename: %r", filename)
        return None

    if not BACKUP_NAME_PATTERN.fullmatch(filename):
        logger.warning("Rejected backup path with invalid filename pattern: %r", filename)
        return None

    backup_root = Path(backup_dir).resolve()
    path = (backup_root / filename).resolve()

    try:
        path.relative_to(backup_root)
    except ValueError:
        logger.warning("Rejected backup path outside backup directory: %r", filename)
        return None

    return path


def is_valid_sqlite_backup(path):
    """Check that a backup file is a healthy SQLite database with schema content."""
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


def get_latest_valid_backup_path(db_path, backup_dir, base_dir):
    """Return the newest valid managed backup or data fallback file."""
    candidates = []
    active_db_path = Path(db_path).resolve()

    backup_directory = Path(backup_dir)
    if backup_directory.exists():
        for path in sorted(
            backup_directory.glob("*.db"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            if path.resolve() == active_db_path:
                continue
            if is_valid_sqlite_backup(path):
                candidates.append(path)

    fallback_path = Path(base_dir) / "data" / "acg_ratings.db.bak"
    if (
        fallback_path.exists()
        and fallback_path.resolve() != active_db_path
        and is_valid_sqlite_backup(fallback_path)
    ):
        candidates.append(fallback_path)

    unique_paths = {path.resolve(): path for path in candidates}
    if not unique_paths:
        return None

    return max(unique_paths.values(), key=lambda item: item.stat().st_mtime)


def rebuild_players_fts_artifacts(conn):
    """Drop legacy player FTS objects so migrations can rebuild them."""
    conn.execute("DROP TRIGGER IF EXISTS players_fts_ai")
    conn.execute("DROP TRIGGER IF EXISTS players_fts_ad")
    conn.execute("DROP TRIGGER IF EXISTS players_fts_au")
    conn.execute("DROP TABLE IF EXISTS players_fts")


def ensure_players_fts_artifacts(conn):
    """Create the player FTS index and synchronization triggers."""
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
    conn.execute("INSERT INTO players_fts(players_fts) VALUES('rebuild')")


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


def restore_db_from_backup(path, db_path):
    """Restore the canonical database and re-run its schema migrations."""
    if path is None or not Path(path).is_file() or not is_valid_sqlite_backup(path):
        return False

    backup_path = Path(path).resolve()
    active_db_path = Path(db_path).resolve()
    if backup_path == active_db_path:
        logger.warning(
            "Skipping backup restore because the selected backup matches the active database: %s",
            path,
        )
        return False

    shutil.copy2(path, db_path)

    from app import (
        bootstrap_default_admin_account,
        ensure_player_schema_columns,
        migrate_handicap_schema,
        migrate_application_settings_schema,
        migrate_auth_schema,
        migrate_config_schema,
        migrate_match_result_schema,
        migrate_matches_notes_schema,
        migrate_tournament_match_identity_schema,
        migrate_tournament_schema,
        normalize_match_round_values,
        repair_legacy_players_table,
    )
    from services.sgf_service import clear_missing_sgf_links, ensure_sgf_schema, restore_sgf_files

    restore_sgf_files(backup_path)
    conn = sqlite3.connect(db_path)
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
        ensure_sgf_schema(conn)
        migrate_matches_notes_schema(conn)
        migrate_match_result_schema(conn)
        migrate_tournament_match_identity_schema(conn)
        normalize_match_round_values(conn)
        migrate_handicap_schema(conn)
        ensure_sgf_schema(conn)
        clear_missing_sgf_links(conn)
        conn.commit()
    finally:
        conn.close()

    return True
