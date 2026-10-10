import os
import shutil
import sqlite3

import pytest

import app as app_module
import routes.admin as admin_routes
import services.backup_service as backup_service
import services.sgf_service as sgf_service


def _insert_player(db_path, first_name, last_name):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO players (first_name, last_name) VALUES (?, ?)",
            (first_name, last_name),
        )
        conn.commit()
    finally:
        conn.close()


def _has_live_player(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM players WHERE first_name = 'Live' AND last_name = 'Only'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


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

    (sgf_dir / "a.sgf").write_text("live", encoding="utf-8")
    backup_path = backup_dir / "source.db"
    shutil.copy2(live_db, backup_path)
    sidecar = backup_path.with_suffix(".sgf")
    sidecar.mkdir()
    (sidecar / "a.sgf").write_text("backup", encoding="utf-8")
    (sidecar / "b.sgf").write_text("backup", encoding="utf-8")

    _insert_player(live_db, "Live", "Only")
    return live_db, backup_path, sgf_dir


def test_restore_replaces_live_database_and_sgf_library(restore_env):
    live_db, backup_path, sgf_dir = restore_env

    assert backup_service.restore_db_from_backup(backup_path, live_db) is True

    assert not _has_live_player(live_db)
    assert (sgf_dir / "a.sgf").read_text(encoding="utf-8") == "backup"
    assert (sgf_dir / "b.sgf").read_text(encoding="utf-8") == "backup"
    assert _staging_files(live_db) == []


def test_failed_migration_keeps_live_database_and_sgf_library(restore_env, monkeypatch):
    live_db, backup_path, sgf_dir = restore_env

    def failing_migration(conn):
        raise RuntimeError("migration failed")

    monkeypatch.setattr(app_module, "migrate_database_schema", failing_migration)

    assert backup_service.restore_db_from_backup(backup_path, live_db) is False

    assert _has_live_player(live_db)
    assert (sgf_dir / "a.sgf").read_text(encoding="utf-8") == "live"
    assert not (sgf_dir / "b.sgf").exists()
    assert _staging_files(live_db) == []


def test_failed_swap_reverts_sgf_library_and_removes_staging_file(restore_env, monkeypatch):
    live_db, backup_path, sgf_dir = restore_env

    def locked_replace(src, dst):
        raise PermissionError("database is locked")

    monkeypatch.setattr(backup_service.os, "replace", locked_replace)

    assert backup_service.restore_db_from_backup(backup_path, live_db) is False

    assert _has_live_player(live_db)
    assert (sgf_dir / "a.sgf").read_text(encoding="utf-8") == "live"
    assert not (sgf_dir / "b.sgf").exists()
    assert _staging_files(live_db) == []


def test_reversible_sgf_restore_can_be_reverted(restore_env):
    _, backup_path, sgf_dir = restore_env

    revert = sgf_service.restore_sgf_files_reversibly(backup_path)
    assert (sgf_dir / "a.sgf").read_text(encoding="utf-8") == "backup"
    assert (sgf_dir / "b.sgf").read_text(encoding="utf-8") == "backup"

    revert()
    assert (sgf_dir / "a.sgf").read_text(encoding="utf-8") == "live"
    assert not (sgf_dir / "b.sgf").exists()


def test_reversible_sgf_restore_undoes_partial_copy(restore_env, monkeypatch):
    _, backup_path, sgf_dir = restore_env
    real_copy2 = shutil.copy2
    copy_calls = []

    def flaky_copy(src, dst, *args, **kwargs):
        copy_calls.append(dst)
        if len(copy_calls) == 2:
            raise OSError("disk full")
        return real_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(sgf_service.shutil, "copy2", flaky_copy)

    with pytest.raises(OSError):
        sgf_service.restore_sgf_files_reversibly(backup_path)

    assert (sgf_dir / "a.sgf").read_text(encoding="utf-8") == "live"
    assert not (sgf_dir / "b.sgf").exists()


def test_restore_route_snapshots_live_database_before_restoring(admin_client, restore_env):
    live_db, backup_path, _ = restore_env

    response = admin_client.post("/admin/backups/restore?lang=en", data={"name": "source.db"})

    assert response.status_code == 302
    snapshots = list(backup_path.parent.glob("*-pre-restore-*.db"))
    assert len(snapshots) == 1
    assert admin_routes.is_valid_sqlite_backup(snapshots[0])
    assert snapshots[0].with_suffix(".sgf").is_dir()
    assert _has_live_player(snapshots[0]) is True
    assert not _has_live_player(live_db)


def test_restore_route_aborts_when_snapshot_cannot_be_verified(
    admin_client, restore_env, monkeypatch
):
    live_db, backup_path, _ = restore_env
    real_is_valid = admin_routes.is_valid_sqlite_backup

    def reject_snapshots(path):
        return "pre-restore" not in str(path) and real_is_valid(path)

    monkeypatch.setattr(admin_routes, "is_valid_sqlite_backup", reject_snapshots)

    response = admin_client.post("/admin/backups/restore?lang=en", data={"name": "source.db"})

    assert response.status_code == 302
    assert _has_live_player(live_db)
    assert list(backup_path.parent.glob("*-pre-restore-*")) == []


def test_restore_route_removes_snapshot_when_restore_fails(
    admin_client, restore_env, monkeypatch
):
    live_db, backup_path, _ = restore_env
    monkeypatch.setattr(admin_routes, "restore_db_from_backup", lambda path: False)

    response = admin_client.post("/admin/backups/restore?lang=en", data={"name": "source.db"})

    assert response.status_code == 302
    assert _has_live_player(live_db)
    assert list(backup_path.parent.glob("*-pre-restore-*")) == []
