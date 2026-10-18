"""Database backup discovery, validation, and restoration helpers."""
import logging
import os
import re
import shutil
import sqlite3
import uuid
from pathlib import Path

from services.alert_service import send_admin_alert

logger = logging.getLogger(__name__)
BACKUP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.db$")
SCHEDULED_BACKUP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}-\d{6}-scheduled-[0-9a-f]{8}\.db$"
)


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
            first_name,
            last_name,
            custom_display_name,
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
            INSERT INTO players_fts(rowid, id, first_name, last_name, custom_display_name,
                                    country, club, slug)
            VALUES (new.id, new.id, new.first_name, new.last_name, new.custom_display_name,
                    new.country, new.club, new.slug);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS players_fts_ad AFTER DELETE ON players BEGIN
            INSERT INTO players_fts(players_fts, rowid, id, first_name, last_name,
                                    custom_display_name, country, club, slug)
            VALUES('delete', old.id, old.id, old.first_name, old.last_name,
                   old.custom_display_name, old.country, old.club, old.slug);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS players_fts_au AFTER UPDATE ON players BEGIN
            INSERT INTO players_fts(players_fts, rowid, id, first_name, last_name,
                                    custom_display_name, country, club, slug)
            VALUES('delete', old.id, old.id, old.first_name, old.last_name,
                   old.custom_display_name, old.country, old.club, old.slug);
            INSERT INTO players_fts(rowid, id, first_name, last_name, custom_display_name,
                                    country, club, slug)
            VALUES (new.id, new.id, new.first_name, new.last_name, new.custom_display_name,
                    new.country, new.club, new.slug);
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


def remove_backup(backup_path):
    """Delete a backup database. Older backups may also have an SGF folder, which is removed too."""
    backup_path = Path(backup_path)
    backup_path.unlink(missing_ok=True)
    legacy_sidecar_path = backup_path.with_suffix(".sgf")
    if legacy_sidecar_path.is_dir():
        shutil.rmtree(legacy_sidecar_path)


def list_scheduled_backups(backup_dir):
    """Return scheduled backups newest first; the timestamp prefix makes names sort by age."""
    directory = Path(backup_dir)
    if not directory.is_dir():
        return []
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and SCHEDULED_BACKUP_PATTERN.fullmatch(path.name)
        ),
        key=lambda path: path.name,
        reverse=True,
    )


def prune_scheduled_backups(backup_dir, keep):
    """Delete scheduled backups beyond the newest ``keep`` and return their filenames."""
    removed = []
    for path in list_scheduled_backups(backup_dir)[keep:]:
        remove_backup(path)
        removed.append(path.name)
    return removed


def verify_backup_restore_path(backup_path, scratch_dir):
    """Run the restore preparation (copy, migrate, verify) on a scratch copy of a backup.

    Raises on any failure. The backup itself and the live database are not modified.
    """
    _prepare_staged_database(Path(backup_path), Path(scratch_dir) / "restore-check.db")


def _prepare_staged_database(backup_path, staging_path):
    """Copy a backup to ``staging_path`` and bring the copy up to the current schema."""
    from app import migrate_database_schema
    from services.auth_service import invalidate_all_user_sessions

    source_conn = sqlite3.connect(backup_path)
    try:
        staging_conn = sqlite3.connect(staging_path)
        try:
            source_conn.backup(staging_conn)
        finally:
            staging_conn.close()
    finally:
        source_conn.close()

    conn = sqlite3.connect(staging_path)
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

    if not is_valid_sqlite_backup(staging_path):
        raise sqlite3.DatabaseError("Staged restore failed integrity verification")


def restore_db_from_backup(path, db_path):
    """Replace the active database with a verified backup, or leave it unchanged.

    The backup is copied and migrated in a staging file beside the active database,
    verified, and only then swapped in with ``os.replace``. Any failure before the
    swap deletes the staging file. SGF files are never changed by a restore; match
    and submission links to files that are missing from disk are cleared by the
    migrations.
    """
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

    staging_path = active_db_path.with_name(
        f".{active_db_path.stem}.restore-{uuid.uuid4().hex}.tmp"
    )
    try:
        _prepare_staged_database(backup_path, staging_path)
        os.replace(staging_path, active_db_path)
    except Exception as exc:
        logger.exception(
            "Backup restore failed; the active database was left unchanged: %s", path
        )
        staging_path.unlink(missing_ok=True)
        send_admin_alert(
            "backup_restore_failure",
            "A database backup could not be restored; the active database was left unchanged.",
            f"Failure type: {type(exc).__name__}",
        )
        return False

    return True
