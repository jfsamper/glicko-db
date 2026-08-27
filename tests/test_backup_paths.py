from pathlib import Path

import routes.admin as admin


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
