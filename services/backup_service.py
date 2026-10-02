"""Database backup discovery, validation, and restoration helpers."""
import logging
import os
import re
import shutil
import sqlite3
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)
BACKUP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.db$")


def ensure_backup_dir(backup_dir):
    os.makedirs(backup_dir, exist_ok=True)


def get_backup_path(filename, backup_dir):
    """Return a safe, server-generated backup path or ``None``."""
    if not isinstance(filename, str):
        logger.warning(
            "Rejected backup path with non-string filename: %r", filename)
        return None

    if not BACKUP_NAME_PATTERN.fullmatch(filename):
        logger.warning(
            "Rejected backup path with invalid filename pattern: %r", filename)
        return None

    backup_root = Path(backup_dir).resolve()
    path = (backup_root / filename).resolve()

    try:
        path.relative_to(backup_root)
    except ValueError:
        logger.warning(
            "Rejected backup path outside backup directory: %r", filename)
        return None

    return path


def is_valid_sqlite_backup(path):
    """Check that a backup file is a healthy SQLite database with schema content."""
    if path is None or not Path(path).is_file():
        return False

    conn = None
    try:
        conn = sqlite3.connect(path)
        table_count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0]
        if table_count <= 0:
            return False

        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        return integrity is not None and integrity[0] == "ok"
    except sqlite3.DatabaseError:
        return False
    finally:
        if conn is not None:
            conn.close()


def create_verified_backup(db_path, backup_dir, filename):
    """Create an atomic SQLite snapshot and verify its integrity before publishing it."""
    ensure_backup_dir(backup_dir)
    backup_path = get_backup_path(filename, backup_dir)
    if backup_path is None:
        raise ValueError("Invalid backup filename")
    if backup_path.exists():
        raise FileExistsError("Backup already exists")

    staging_path = backup_path.with_name(
        f".{backup_path.stem}.{uuid.uuid4().hex}.tmp"
    )
    source_conn = None
    staging_conn = None
    try:
        source_conn = sqlite3.connect(db_path)
        staging_conn = sqlite3.connect(staging_path)
        source_conn.backup(staging_conn)
        staging_conn.commit()
        staging_conn.close()
        staging_conn = None
        source_conn.close()
        source_conn = None
        if not is_valid_sqlite_backup(staging_path):
            raise sqlite3.DatabaseError("Staged backup failed integrity verification")
        os.replace(staging_path, backup_path)
        if not is_valid_sqlite_backup(backup_path):
            raise sqlite3.DatabaseError("Published backup failed integrity verification")
        return backup_path
    except Exception:
        if staging_conn is not None:
            staging_conn.close()
        if source_conn is not None:
            source_conn.close()
        staging_path.unlink(missing_ok=True)
        backup_path.unlink(missing_ok=True)
        raise


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

    from app import migrate_database_schema
    from services.auth_service import invalidate_all_user_sessions
    from services.sgf_service import restore_sgf_files

    restore_sgf_files(backup_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        migrate_database_schema(conn)
        invalidate_all_user_sessions(conn)
        rebuild_players_fts_artifacts(conn)
        ensure_players_fts_artifacts(conn)
        ensure_rating_state_table(conn)
        conn.commit()
    finally:
        conn.close()

    return True
