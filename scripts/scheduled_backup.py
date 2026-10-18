"""Create, verify, and prune scheduled database backups.

Run from Windows Task Scheduler or cron, for example:
    python scripts/scheduled_backup.py
Exit status is 0 when the new backup verified, and 1 otherwise.
"""
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from services.alert_service import prime_admin_alert_recipients, send_admin_alert
from services.scheduled_backup_service import run_scheduled_backup

logger = logging.getLogger("scheduled_backup")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    prime_admin_alert_recipients()
    backup_dir = os.path.join(config.BASE_DIR, "backups")
    try:
        result = run_scheduled_backup(
            config.DB_PATH, backup_dir, config.SCHEDULED_BACKUP_KEEP
        )
    except Exception as exc:
        logger.exception("Scheduled backup failed; no older backups were removed")
        send_admin_alert(
            "scheduled_backup_failure",
            "The scheduled database backup failed; older backups were not removed.",
            f"Failure type: {type(exc).__name__}",
        )
        return 1

    logger.info(
        "Scheduled backup %s verified; removed %d older scheduled backup(s)",
        result["filename"],
        len(result["pruned"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
