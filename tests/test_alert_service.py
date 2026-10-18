import logging
import sqlite3
import time
from types import SimpleNamespace

import routes.admin as admin_routes
import services.alert_service as alert_service
import services.backup_service as backup_service
import services.rating_service as rating_service
import pytest


def test_repeated_login_limit_is_recorded_once_per_window_and_alerted(
        app, monkeypatch):
    moments = iter([100, 101, 102, 103, 200, 201, 202])
    sent_alerts = []
    monkeypatch.setattr(
        admin_routes,
        "_login_attempt_settings",
        lambda conn: {
            "max_login_attempts": 2,
            "login_window_seconds": 60,
        },
    )
    monkeypatch.setattr(
        admin_routes,
        "time",
        SimpleNamespace(time=lambda: next(moments)),
    )
    monkeypatch.setattr(
        admin_routes,
        "send_admin_alert",
        lambda *args: sent_alerts.append(args),
    )

    with app.app_context():
        allowed_results = [
            admin_routes.reserve_login_attempt("192.0.2.10", "TargetAccount")
            for _ in range(7)
        ]

    assert allowed_results == [True, True, False, False, True, True, False]
    assert len(sent_alerts) == 1
    assert sent_alerts[0][0] == "login_rate_limit_repeated"
    assert "192.0.2.10" in sent_alerts[0][2]
    assert "ip, username" in sent_alerts[0][2]

    conn = admin_routes.get_db()
    try:
        stored_hashes = {
            row[0] for row in conn.execute(
                "SELECT source_hash FROM login_alert_events"
            ).fetchall()
        }
        assert len(stored_hashes) == 2
        assert all("TargetAccount" not in value for value in stored_hashes)
    finally:
        conn.close()


def test_login_limit_history_expires_after_30_days(app):
    conn = sqlite3.connect(":memory:")
    source = [("ip", "192.0.2.20")]
    try:
        with app.app_context():
            first = alert_service.record_login_threshold_events(
                conn, source, 100, 60
            )
            after_30_days = alert_service.record_login_threshold_events(
                conn,
                source,
                100 + alert_service.LOGIN_ALERT_LOOKBACK_SECONDS + 1,
                60,
            )

        assert first == []
        assert after_30_days == []
        assert conn.execute(
            "SELECT COUNT(*) FROM login_alert_events"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_alert_email_targets_only_active_administrators_with_email(
        tmp_path, monkeypatch):
    db_path = tmp_path / "alerts.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, is_active INTEGER);
        CREATE TABLE roles (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE user_roles (user_id INTEGER, role_id INTEGER);
        INSERT INTO roles VALUES (1, 'administrator'), (2, 'operator');
        INSERT INTO users VALUES
            (1, 'Admin@Example.com', 1),
            (2, 'disabled@example.com', 0),
            (3, 'operator@example.com', 1),
            (4, 'admin@example.com', 1);
        INSERT INTO user_roles VALUES (1, 1), (2, 1), (3, 2), (4, 1);
        """
    )
    conn.commit()
    conn.close()

    def open_test_db():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    sent_messages = []

    class FakeSMTP:
        def __init__(self, server, port, timeout):
            assert (server, port, timeout) == ("smtp.example.test", 587, 10)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def starttls(self):
            pass

        def login(self, _username, _password):
            pass

        def send_message(self, message):
            sent_messages.append(message)

    monkeypatch.setattr(alert_service, "get_db", open_test_db)
    monkeypatch.setattr(alert_service, "MAIL_SERVER", "smtp.example.test")
    monkeypatch.setattr(alert_service, "MAIL_PORT", 587)
    monkeypatch.setattr(alert_service, "MAIL_USERNAME", "")
    monkeypatch.setattr(alert_service, "MAIL_PASSWORD", "")
    monkeypatch.setattr(alert_service, "MAIL_USE_TLS", True)
    monkeypatch.setattr(alert_service.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(alert_service, "_admin_recipients", ())
    monkeypatch.setattr(alert_service, "_admin_recipients_refreshed_at", None)

    assert alert_service.send_admin_alert(
        "backup_restore_failure",
        "Restore failed.",
        "Failure type: DatabaseError",
    )

    assert len(sent_messages) == 1
    assert sent_messages[0]["To"].casefold() == "admin@example.com"
    assert "disabled@example.com" not in sent_messages[0].as_string()
    assert "operator@example.com" not in sent_messages[0].as_string()


def test_email_delivery_failure_is_logged_and_does_not_raise(monkeypatch, caplog):
    class FailingSMTP:
        def __init__(self, *_args, **_kwargs):
            raise OSError("SMTP unavailable")

    monkeypatch.setattr(alert_service, "_admin_recipients", ("admin@example.com",))
    monkeypatch.setattr(
        alert_service,
        "_admin_recipients_refreshed_at",
        time.monotonic(),
    )
    monkeypatch.setattr(alert_service, "MAIL_SERVER", "smtp.example.test")
    monkeypatch.setattr(alert_service.smtplib, "SMTP", FailingSMTP)

    with caplog.at_level(logging.ERROR, logger="services.alert_service"):
        sent = alert_service.send_admin_alert(
            "rating_replay_failure",
            "Replay failed.",
        )

    assert sent is False
    assert any(
        getattr(record, "event_name", None) == "alerts.delivery.failed"
        for record in caplog.records
    )


def test_cached_admin_recipients_allow_alerts_when_database_is_unavailable(
        monkeypatch):
    sent_messages = []

    class FakeSMTP:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def send_message(self, message):
            sent_messages.append(message)

    def fail_database():
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(alert_service, "get_db", fail_database)
    monkeypatch.setattr(
        alert_service, "_admin_recipients", ("admin@example.com",)
    )
    monkeypatch.setattr(alert_service, "_admin_recipients_refreshed_at", 0)
    monkeypatch.setattr(alert_service, "MAIL_SERVER", "smtp.example.test")
    monkeypatch.setattr(alert_service, "MAIL_USE_TLS", False)
    monkeypatch.setattr(alert_service, "MAIL_USERNAME", "")
    monkeypatch.setattr(alert_service.smtplib, "SMTP", FakeSMTP)

    assert alert_service.send_admin_alert("database_failure", "Database failed.")
    assert sent_messages[0]["To"] == "admin@example.com"


def test_rating_replay_failure_alerts_and_preserves_original_exception(monkeypatch):
    class FakeConnection:
        rolled_back = False
        closed = False

        def execute(self, _statement):
            pass

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    conn = FakeConnection()
    sent_alerts = []

    def fail_replay(_conn, _dirty_date):
        raise sqlite3.OperationalError("replay failed")

    monkeypatch.setattr(rating_service, "get_db", lambda: conn)
    monkeypatch.setattr(
        rating_service, "get_dirty_date", lambda conn: "2026-01-02"
    )
    monkeypatch.setattr(rating_service, "_replay_from_dirty_date", fail_replay)
    monkeypatch.setattr(
        rating_service,
        "send_admin_alert",
        lambda *args: sent_alerts.append(args),
    )

    with pytest.raises(sqlite3.OperationalError, match="replay failed"):
        rating_service.update_from_latest_snapshot()

    assert conn.rolled_back
    assert conn.closed
    assert sent_alerts[0][0] == "rating_replay_failure"


def test_restore_failure_alerts_and_returns_false(monkeypatch, tmp_path):
    backup_path = tmp_path / "backup.db"
    active_path = tmp_path / "active.db"
    backup_path.write_bytes(b"backup")
    sent_alerts = []

    def fail_staging(_backup_path, _staging_path):
        raise sqlite3.DatabaseError("restore failed")

    monkeypatch.setattr(
        backup_service, "is_valid_sqlite_backup", lambda _path: True
    )
    monkeypatch.setattr(
        backup_service, "_prepare_staged_database", fail_staging
    )
    monkeypatch.setattr(
        backup_service,
        "send_admin_alert",
        lambda *args: sent_alerts.append(args),
    )

    assert not backup_service.restore_db_from_backup(backup_path, active_path)
    assert sent_alerts[0][0] == "backup_restore_failure"


def test_checked_backup_failure_alerts_and_preserves_exception(monkeypatch):
    sent_alerts = []

    def fail_backup(_filename):
        raise sqlite3.DatabaseError("backup failed")

    monkeypatch.setattr(admin_routes, "create_verified_backup", fail_backup)
    monkeypatch.setattr(
        admin_routes,
        "send_admin_alert",
        lambda *args: sent_alerts.append(args),
    )

    with pytest.raises(sqlite3.DatabaseError, match="backup failed"):
        admin_routes.create_checked_backup("failed.db")

    assert sent_alerts[0][0] == "backup_creation_failure"
