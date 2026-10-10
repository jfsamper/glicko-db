import re
import shutil
import sqlite3

import pytest

import app as app_module
import routes.admin as admin_routes
import services.backup_service as backup_service
import services.sgf_service as sgf_service


def _execute(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _query_one(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def _has_live_player(db_path):
    return _query_one(
        db_path,
        "SELECT COUNT(*) FROM players WHERE first_name = 'Live' AND last_name = 'Only'",
    ) == 1


def _pointer(db_path, match_id):
    return _query_one(db_path, "SELECT sgf_filename FROM matches WHERE id = ?", (match_id,))


def _staging_files(db_path):
    return list(db_path.parent.glob(f".{db_path.stem}.restore-*.tmp"))


@pytest.fixture
def restore_env(tmp_path, monkeypatch, isolate_test_database):
    live_db = isolate_test_database
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    sgf_dir = tmp_path / "sgf"
    sgf_dir.mkdir()
    monkeypatch.setattr(sgf_service, "SGF_UPLOAD_DIR", sgf_dir)
    monkeypatch.setattr(admin_routes, "BACKUP_DIR", str(backup_dir))

    (sgf_dir / "kept.sgf").write_text("library", encoding="utf-8")
    kept_match = _query_one(live_db, "SELECT MIN(id) FROM matches")
    missing_match = _query_one(live_db, "SELECT MAX(id) FROM matches")
    _execute(live_db, "UPDATE matches SET sgf_filename = 'kept.sgf' WHERE id = ?", (kept_match,))
    _execute(live_db, "UPDATE matches SET sgf_filename = 'gone.sgf' WHERE id = ?", (missing_match,))

    backup_path = backup_dir / "source.db"
    shutil.copy2(live_db, backup_path)
    (sgf_dir / "newer.sgf").write_text("uploaded after the backup", encoding="utf-8")

    _execute(live_db, "INSERT INTO players (first_name, last_name) VALUES ('Live', 'Only')")
    return live_db, backup_path, sgf_dir, kept_match, missing_match


def _assert_live_state_unchanged(live_db, sgf_dir, kept_match, missing_match):
    assert _has_live_player(live_db)
    assert _pointer(live_db, kept_match) == "kept.sgf"
    assert _pointer(live_db, missing_match) == "gone.sgf"
    assert (sgf_dir / "kept.sgf").read_text(encoding="utf-8") == "library"
    assert (sgf_dir / "newer.sgf").read_text(encoding="utf-8") == "uploaded after the backup"
    assert sorted(path.name for path in sgf_dir.iterdir()) == ["kept.sgf", "newer.sgf"]


def test_restore_replaces_database_and_clears_only_missing_sgf_pointers(restore_env):
    live_db, backup_path, sgf_dir, kept_match, missing_match = restore_env

    assert backup_service.restore_db_from_backup(backup_path, live_db) is True

    assert not _has_live_player(live_db)
    assert _pointer(live_db, kept_match) == "kept.sgf"
    assert _pointer(live_db, missing_match) is None
    assert (sgf_dir / "kept.sgf").read_text(encoding="utf-8") == "library"
    assert (sgf_dir / "newer.sgf").read_text(encoding="utf-8") == "uploaded after the backup"
    assert sorted(path.name for path in sgf_dir.iterdir()) == ["kept.sgf", "newer.sgf"]
    assert _staging_files(live_db) == []


def test_failed_migration_keeps_database_and_sgf_library(restore_env, monkeypatch):
    live_db, backup_path, sgf_dir, kept_match, missing_match = restore_env

    def failing_migration(conn):
        raise RuntimeError("migration failed")

    monkeypatch.setattr(app_module, "migrate_database_schema", failing_migration)

    assert backup_service.restore_db_from_backup(backup_path, live_db) is False

    _assert_live_state_unchanged(live_db, sgf_dir, kept_match, missing_match)
    assert _staging_files(live_db) == []


def test_failed_swap_keeps_database_and_sgf_library(restore_env, monkeypatch):
    live_db, backup_path, sgf_dir, kept_match, missing_match = restore_env

    def locked_replace(src, dst):
        raise PermissionError("database is locked")

    monkeypatch.setattr(backup_service.os, "replace", locked_replace)

    assert backup_service.restore_db_from_backup(backup_path, live_db) is False

    _assert_live_state_unchanged(live_db, sgf_dir, kept_match, missing_match)
    assert _staging_files(live_db) == []


def test_restore_route_snapshots_database_before_restoring(admin_client, restore_env):
    live_db, backup_path, _, _, _ = restore_env

    response = admin_client.post("/admin/backups/restore?lang=en", data={"name": "source.db"})

    assert response.status_code == 302
    snapshots = list(backup_path.parent.glob("*-pre-restore-*.db"))
    assert len(snapshots) == 1
    assert admin_routes.is_valid_sqlite_backup(snapshots[0])
    assert not snapshots[0].with_suffix(".sgf").exists()
    assert _has_live_player(snapshots[0])
    assert not _has_live_player(live_db)


def test_restore_route_aborts_when_snapshot_cannot_be_verified(
    admin_client, restore_env, monkeypatch
):
    live_db, backup_path, sgf_dir, kept_match, missing_match = restore_env
    real_is_valid = admin_routes.is_valid_sqlite_backup

    def reject_snapshots(path):
        return "pre-restore" not in str(path) and real_is_valid(path)

    monkeypatch.setattr(admin_routes, "is_valid_sqlite_backup", reject_snapshots)

    response = admin_client.post("/admin/backups/restore?lang=en", data={"name": "source.db"})

    assert response.status_code == 302
    _assert_live_state_unchanged(live_db, sgf_dir, kept_match, missing_match)
    assert list(backup_path.parent.glob("*-pre-restore-*")) == []


def test_restore_route_removes_snapshot_when_restore_fails(
    admin_client, restore_env, monkeypatch
):
    live_db, backup_path, sgf_dir, kept_match, missing_match = restore_env
    monkeypatch.setattr(admin_routes, "restore_db_from_backup", lambda path: False)

    response = admin_client.post("/admin/backups/restore?lang=en", data={"name": "source.db"})

    assert response.status_code == 302
    _assert_live_state_unchanged(live_db, sgf_dir, kept_match, missing_match)
    assert list(backup_path.parent.glob("*-pre-restore-*")) == []


def test_manual_backup_route_creates_verified_database_backup(admin_client, restore_env):
    _, backup_path, _, _, _ = restore_env

    response = admin_client.post("/admin/backups/create?lang=en")

    assert response.status_code == 302
    created = [
        path
        for path in backup_path.parent.glob("*.db")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{6}\.db", path.name)
    ]
    assert len(created) == 1
    assert admin_routes.is_valid_sqlite_backup(created[0])
    assert not created[0].with_suffix(".sgf").exists()


def test_manual_backup_route_leaves_no_file_when_verification_fails(
    admin_client, restore_env, monkeypatch
):
    _, backup_path, _, _, _ = restore_env
    monkeypatch.setattr(admin_routes, "is_valid_sqlite_backup", lambda path: False)

    response = admin_client.post("/admin/backups/create?lang=en")

    assert response.status_code == 302
    assert [path.name for path in backup_path.parent.glob("*.db")] == ["source.db"]
