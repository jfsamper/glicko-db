"""Scheduled backup creation, restore verification, and retention."""
import sqlite3
import tempfile
import uuid

from services.backup_service import (
    create_verified_backup,
    is_valid_sqlite_backup,
    prune_scheduled_backups,
    remove_backup_with_sgf,
    verify_backup_restore_path,
)
from services.sgf_service import backup_sgf_files
from services.timezone_service import current_datetime


def run_scheduled_backup(db_path, backup_dir, keep):
    """Create a scheduled backup, verify that it restores, then apply retention.

    Retention runs only after the new backup verifies, so a failed run never
    removes older good backups. A backup that fails verification is deleted.
    """
    if keep < 1:
        raise ValueError("keep must be at least 1")

    timestamp = current_datetime().strftime("%Y-%m-%d-%H%M%S")
    filename = f"{timestamp}-scheduled-{uuid.uuid4().hex[:8]}.db"
    backup_path = create_verified_backup(db_path, backup_dir, filename)
    try:
        if not is_valid_sqlite_backup(backup_path):
            raise sqlite3.DatabaseError("Scheduled backup failed integrity verification")
        backup_sgf_files(backup_path)
        with tempfile.TemporaryDirectory(prefix="scheduled-restore-check-") as scratch_dir:
            verify_backup_restore_path(backup_path, scratch_dir)
    except Exception:
        remove_backup_with_sgf(backup_path)
        raise

    return {
        "filename": filename,
        "pruned": prune_scheduled_backups(backup_dir, keep),
    }
