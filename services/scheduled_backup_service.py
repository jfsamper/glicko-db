"""Scheduled backup creation, restore verification, and retention."""
import sqlite3
import tempfile
import uuid

from services.backup_service import (
    create_verified_backup,
    is_valid_sqlite_backup,
    prune_scheduled_backups,
    remove_backup,
    verify_backup_restore_path,
)
from services.timezone_service import current_datetime
from services.operational_status import record_operational_event_safely


def run_scheduled_backup(db_path, backup_dir, keep):
    """Create a scheduled backup, verify that it restores, then apply retention.

    Retention runs only after the new backup verifies, so a failed run never
    removes older good backups. A backup that fails verification is deleted.
    """
    if keep < 1:
        record_operational_event_safely(
            "scheduled_backup_failed",
            {
                "stage": "configuration",
                "failure_type": "InvalidRetentionLimit",
            },
            db_path=db_path,
        )
        raise ValueError("keep must be at least 1")

    timestamp = current_datetime().strftime("%Y-%m-%d-%H%M%S")
    filename = f"{timestamp}-scheduled-{uuid.uuid4().hex[:8]}.db"
    try:
        backup_path = create_verified_backup(db_path, backup_dir, filename)
    except Exception as exc:
        record_operational_event_safely(
            "scheduled_backup_failed",
            {"failure_type": type(exc).__name__},
            db_path=db_path,
        )
        raise

    try:
        if not is_valid_sqlite_backup(backup_path):
            raise sqlite3.DatabaseError("Scheduled backup failed integrity verification")
        with tempfile.TemporaryDirectory(prefix="scheduled-restore-check-") as scratch_dir:
            verify_backup_restore_path(backup_path, scratch_dir)
    except Exception as exc:
        remove_backup(backup_path)
        record_operational_event_safely(
            "restore_check_failed",
            {
                "backup": filename,
                "failure_type": type(exc).__name__,
            },
            db_path=db_path,
        )
        record_operational_event_safely(
            "scheduled_backup_failed",
            {
                "backup": filename,
                "stage": "restore_check",
                "failure_type": type(exc).__name__,
            },
            db_path=db_path,
        )
        raise

    record_operational_event_safely(
        "restore_check_succeeded",
        {"backup": filename},
        db_path=db_path,
    )

    try:
        pruned = prune_scheduled_backups(backup_dir, keep)
    except Exception as exc:
        record_operational_event_safely(
            "backup_retention_failed",
            {
                "backup": filename,
                "failure_type": type(exc).__name__,
            },
            db_path=db_path,
        )
        record_operational_event_safely(
            "scheduled_backup_failed",
            {
                "backup": filename,
                "stage": "retention",
                "failure_type": type(exc).__name__,
            },
            db_path=db_path,
        )
        raise

    record_operational_event_safely(
        "scheduled_backup_succeeded",
        {
            "backup": filename,
            "pruned_count": len(pruned),
            "retained": keep,
        },
        db_path=db_path,
    )
    return {
        "filename": filename,
        "pruned": pruned,
    }
