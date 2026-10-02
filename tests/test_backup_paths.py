import sqlite3
from pathlib import Path

import routes.admin as admin
from services.backup_service import create_verified_backup, is_valid_sqlite_backup


def test_backup_paths_only_allow_server_generated_files(monkeypatch, tmp_path):
    monkeypatch.setattr(admin, "BACKUP_DIR", str(tmp_path))

    assert admin.get_backup_path("2026-08-13-120000.db") == (
        tmp_path / "2026-08-13-120000.db"
    ).resolve()
    assert admin.get_backup_path("live_8-26.db") == (
        tmp_path / "live_8-26.db"
    ).resolve()
    assert admin.get_backup_path("..\\data\\acg_ratings.db") is None
    assert admin.get_backup_path("../../data/acg_ratings.db") is None
    assert admin.get_backup_path(str(Path(tmp_path).resolve() / "backup.db")) is None
    assert admin.get_backup_path("backup.txt") is None


def test_backup_path_rejections_emit_warning_logs(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(admin, "BACKUP_DIR", str(tmp_path))

    caplog.set_level("WARNING", logger="routes.admin")

    assert admin.get_backup_path("../../data/acg_ratings.db") is None
    assert admin.get_backup_path("backup.txt") is None
    assert admin.get_backup_path(None) is None

    warning_messages = [record.getMessage() for record in caplog.records]
    assert any("invalid filename pattern" in message for message in warning_messages)
    assert any("non-string filename" in message for message in warning_messages)


def test_verified_backup_publishes_a_valid_sqlite_snapshot(tmp_path):
    source_path = tmp_path / "live.db"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(source_path) as conn:
        conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker VALUES ('before replacement')")

    backup_path = create_verified_backup(
        source_path, backup_dir, "2026-10-02-pre-import.db"
    )

    assert backup_path.is_file()
    assert is_valid_sqlite_backup(backup_path)
    with sqlite3.connect(backup_path) as conn:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "before replacement"
