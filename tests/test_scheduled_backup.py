import shutil

import pytest

import scripts.scheduled_backup as scheduled_cli
import services.backup_service as backup_service
import services.scheduled_backup_service as scheduled_service
import services.sgf_service as sgf_service


@pytest.fixture
def backup_env(tmp_path, monkeypatch, isolate_test_database):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    sgf_dir = tmp_path / "sgf"
    sgf_dir.mkdir()
    monkeypatch.setattr(sgf_service, "SGF_UPLOAD_DIR", sgf_dir)
    return isolate_test_database, backup_dir


def _seed_scheduled(backup_dir, source_db, index, legacy_sidecar=False):
    path = backup_dir / f"2026-01-{index:02d}-120000-scheduled-{index:08x}.db"
    shutil.copy2(source_db, path)
    if legacy_sidecar:
        path.with_suffix(".sgf").mkdir()
    return path


def test_scheduled_run_creates_verified_backup_without_sgf_sidecar(backup_env):
    live_db, backup_dir = backup_env

    result = scheduled_service.run_scheduled_backup(live_db, backup_dir, keep=14)

    created = backup_dir / result["filename"]
    assert backup_service.SCHEDULED_BACKUP_PATTERN.fullmatch(result["filename"])
    assert backup_service.is_valid_sqlite_backup(created)
    assert not created.with_suffix(".sgf").exists()
    assert result["pruned"] == []


def test_retention_keeps_newest_scheduled_and_leaves_other_backups(backup_env):
    live_db, backup_dir = backup_env
    seeds = [
        _seed_scheduled(backup_dir, live_db, index, legacy_sidecar=(index == 1))
        for index in range(1, 5)
    ]
    manual = backup_dir / "2026-01-01-120000.db"
    pre_restore = backup_dir / "2026-01-01-120000-pre-restore-abcdef12.db"
    shutil.copy2(live_db, manual)
    shutil.copy2(live_db, pre_restore)

    result = scheduled_service.run_scheduled_backup(live_db, backup_dir, keep=3)

    assert sorted(result["pruned"]) == sorted([seeds[0].name, seeds[1].name])
    remaining = backup_service.list_scheduled_backups(backup_dir)
    assert [path.name for path in remaining] == [
        result["filename"],
        seeds[3].name,
        seeds[2].name,
    ]
    assert not seeds[0].exists()
    assert not seeds[0].with_suffix(".sgf").exists()
    assert manual.exists()
    assert pre_restore.exists()


def test_failed_verification_removes_new_backup_and_prunes_nothing(backup_env, monkeypatch):
    live_db, backup_dir = backup_env
    seeds = [_seed_scheduled(backup_dir, live_db, index) for index in range(1, 3)]

    def failing_preparation(backup_path, staging_path):
        raise RuntimeError("migration failed")

    monkeypatch.setattr(backup_service, "_prepare_staged_database", failing_preparation)

    with pytest.raises(RuntimeError):
        scheduled_service.run_scheduled_backup(live_db, backup_dir, keep=1)

    assert backup_service.list_scheduled_backups(backup_dir) == [seeds[1], seeds[0]]
    assert len(list(backup_dir.glob("*.db"))) == 2


def test_scheduled_run_rejects_keep_below_one(backup_env):
    live_db, backup_dir = backup_env

    with pytest.raises(ValueError):
        scheduled_service.run_scheduled_backup(live_db, backup_dir, keep=0)

    assert list(backup_dir.iterdir()) == []


def test_cli_exits_zero_when_scheduled_backup_succeeds(monkeypatch):
    def successful_run(db_path, backup_dir, keep):
        return {"filename": "2026-10-10-170000-scheduled-00000001.db", "pruned": []}

    monkeypatch.setattr(scheduled_cli, "run_scheduled_backup", successful_run)

    assert scheduled_cli.main() == 0


def test_cli_exits_one_when_scheduled_backup_fails(monkeypatch):
    def failing_run(db_path, backup_dir, keep):
        raise RuntimeError("verification failed")

    monkeypatch.setattr(scheduled_cli, "run_scheduled_backup", failing_run)

    assert scheduled_cli.main() == 1
