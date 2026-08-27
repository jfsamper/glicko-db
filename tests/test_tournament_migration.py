import sqlite3
from datetime import datetime
from pathlib import Path

import conftest
from app import app, migrate_matches_notes_schema, migrate_tournament_schema, normalize_match_round_values
import routes.admin as admin_routes
import services.common as common


def table_columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_migration_creates_missing_tournament_tables():
    conn = sqlite3.connect(":memory:")

    migrate_tournament_schema(conn)

    assert {"tournaments", "tournament_participants", "tournament_rounds", "tournament_round_players", "tournament_pairings"} <= {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "tournament_type" in table_columns(conn, "tournaments")
    assert "is_bye" in table_columns(conn, "tournament_pairings")
    indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "idx_tournament_round_players_round_status" in indexes


def test_match_round_migration_backfills_numeric_keys_without_replacing_notes():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            event TEXT,
            notes TEXT
        );
        INSERT INTO matches
            (id, match_date, white_player_id, black_player_id, result, notes)
        VALUES
            (1, '2026-08-01', 1, 2, '1-0', 'Round 3'),
            (2, '2026-08-01', 1, 2, '1-0', '14:00:00'),
            (3, '2026-08-01', 1, 2, '1-0', 'Unknown note'),
            (4, '2026-08-01', 1, 2, '1-0', NULL);
        """
    )

    migrate_matches_notes_schema(conn)
    normalize_match_round_values(conn)

    rows = conn.execute(
        "SELECT notes, round_number FROM matches ORDER BY id"
    ).fetchall()
    assert [(row["notes"], row["round_number"]) for row in rows] == [
        ("Round 3", 3),
        ("14:00:00", 2),
        ("Unknown note", 0),
        (None, 0),
    ]
    conn.close()


def test_migration_upgrades_partial_legacy_tournament_tables():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE tournaments (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE tournament_participants (
            id INTEGER PRIMARY KEY
        );
        CREATE TABLE tournament_rounds (
            id INTEGER PRIMARY KEY
        );
        CREATE TABLE tournament_pairings (
            id INTEGER PRIMARY KEY
        );
        INSERT INTO tournaments (id, name) VALUES (1, 'Legacy event');
        """
    )

    migrate_tournament_schema(conn)

    assert {
        "tournament_type", "pairing_system", "status", "rounds", "created_at"
    } <= table_columns(conn, "tournaments")
    assert {"seed_rating", "initial_score", "received_bye"} <= table_columns(conn, "tournament_participants")
    assert {"tournament_id", "player_id"} <= table_columns(conn, "tournament_participants")
    assert {"tournament_id", "round_number"} <= table_columns(conn, "tournament_rounds")
    assert "status" in table_columns(conn, "tournament_rounds")
    assert {"board_number", "black_player_id", "result", "is_bye"} <= table_columns(conn, "tournament_pairings")
    assert {"round_id", "white_player_id"} <= table_columns(conn, "tournament_pairings")
    row = conn.execute("SELECT tournament_type, pairing_system FROM tournaments WHERE id = 1").fetchone()
    assert row == ("swiss", "swiss")
    migrate_tournament_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0] == 1


def test_restore_backup_runs_migration_for_missing_tournament_schema(tmp_path, monkeypatch):
    current_db = tmp_path / "current.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    conn = sqlite3.connect(current_db)
    conn.execute(
        "CREATE TABLE players (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO players (display_name) VALUES ('Alpha')"
    )
    conn.commit()
    conn.close()

    backup_name = f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.db"
    backup_path = backup_dir / backup_name
    backup_conn = sqlite3.connect(backup_path)
    backup_conn.execute(
        "CREATE TABLE players (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL)"
    )
    backup_conn.execute(
        "INSERT INTO players (display_name) VALUES ('Old Alpha')"
    )
    backup_conn.commit()
    backup_conn.close()

    monkeypatch.setattr(admin_routes, "DB_PATH", str(current_db))
    monkeypatch.setattr(admin_routes, "BACKUP_DIR", str(backup_dir))

    app.testing = True
    client = app.test_client()
    conftest.set_admin_session(client, current_db)

    response = client.post("/admin/backups/restore", data={"name": backup_name})

    assert response.status_code == 302

    restored = sqlite3.connect(current_db)
    tables = {
        row[0]
        for row in restored.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"tournaments", "tournament_participants", "tournament_rounds", "tournament_round_players", "tournament_pairings"} <= tables
    restored.close()


def test_restore_backup_backfills_match_round_numbers(tmp_path, monkeypatch):
    current_db = tmp_path / "current.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    backup_name = f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.db"
    backup_path = backup_dir / backup_name
    backup_conn = sqlite3.connect(backup_path)
    backup_conn.executescript(
        """
        CREATE TABLE players (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL);
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            event TEXT,
            notes TEXT
        );
        INSERT INTO players (id, display_name) VALUES (1, 'Alice'), (2, 'Bob');
        INSERT INTO matches
            (id, match_date, white_player_id, black_player_id, result, notes)
        VALUES (1, '2026-08-17', 1, 2, '1-0', '14:00:00');
        """
    )
    backup_conn.commit()
    backup_conn.close()

    monkeypatch.setattr(admin_routes, "DB_PATH", str(current_db))
    monkeypatch.setattr(admin_routes, "BACKUP_DIR", str(backup_dir))

    assert admin_routes.restore_db_from_backup(backup_path)

    restored = sqlite3.connect(current_db)
    row = restored.execute(
        "SELECT event, notes, round_number FROM matches WHERE id = 1"
    ).fetchone()
    assert row == (None, "14:00:00", 2)
    restored.execute(
        "UPDATE matches SET event = ?, notes = ?, round_number = ? WHERE id = ?",
        ("Legacy event", "Round 3", 3, 1),
    )
    restored.commit()
    assert restored.execute(
        "SELECT event, notes, round_number FROM matches WHERE id = 1"
    ).fetchone() == ("Legacy event", "Round 3", 3)
    restored.close()


def test_restore_backup_rebuilds_legacy_players_fts_objects(tmp_path, monkeypatch):
    current_db = tmp_path / "current.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    backup_name = f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.db"
    backup_path = backup_dir / backup_name
    backup_conn = sqlite3.connect(backup_path)
    backup_conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            display_name TEXT,
            country TEXT,
            club TEXT,
            slug TEXT,
            active INTEGER DEFAULT 1,
            rating REAL DEFAULT 1500,
            rd REAL DEFAULT 350,
            volatility REAL DEFAULT 0.06,
            games_played INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            draws INTEGER DEFAULT 0,
            initial_rating REAL
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            event TEXT,
            notes TEXT,
            round_number INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            rating REAL NOT NULL,
            rd REAL NOT NULL,
            volatility REAL NOT NULL
        );
        INSERT INTO players (id, first_name, last_name, display_name)
        VALUES (1, 'Alice', 'A', 'Alice');
        INSERT INTO players (id, first_name, last_name, display_name)
        VALUES (2, 'Bob', 'B', 'Bob');
        INSERT INTO matches (id, match_date, white_player_id, black_player_id, result)
        VALUES (1, '2026-08-17', 1, 2, '1-0');

        CREATE TABLE players_fts (
            rowid INTEGER,
            display_name TEXT,
            country TEXT,
            club TEXT,
            slug TEXT
        );
        CREATE TRIGGER players_fts_ai AFTER INSERT ON players BEGIN
            SELECT RAISE(ABORT, 'legacy players_fts_ai trigger');
        END;
        CREATE TRIGGER players_fts_ad AFTER DELETE ON players BEGIN
            SELECT RAISE(ABORT, 'legacy players_fts_ad trigger');
        END;
        CREATE TRIGGER players_fts_au AFTER UPDATE ON players BEGIN
            SELECT RAISE(ABORT, 'legacy players_fts_au trigger');
        END;
        """
    )
    backup_conn.commit()
    backup_conn.close()

    monkeypatch.setattr(admin_routes, "DB_PATH", str(current_db))
    monkeypatch.setattr(admin_routes, "BACKUP_DIR", str(backup_dir))

    assert admin_routes.restore_db_from_backup(backup_path)

    restored = sqlite3.connect(current_db)
    restored.row_factory = sqlite3.Row
    try:
        restored.execute("UPDATE players SET wins = wins")
        restored.commit()
        # The rebuilt virtual table should accept integrity checks after restore.
        restored.execute("INSERT INTO players_fts(players_fts) VALUES('integrity-check')")

        trigger_sql = {
            row["name"]: row["sql"]
            for row in restored.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='players'"
            )
        }
        assert "players_fts_au" in trigger_sql
        assert "legacy players_fts_au trigger" not in (trigger_sql["players_fts_au"] or "")
    finally:
        restored.close()


def test_restore_backup_repairs_legacy_players_corrupt_table(tmp_path, monkeypatch):
    current_db = tmp_path / "current.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    backup_name = f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.db"
    backup_path = backup_dir / backup_name
    backup_conn = sqlite3.connect(backup_path)
    backup_conn.executescript(
        """
        CREATE TABLE players_corrupt (
            id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            display_name TEXT,
            rating REAL,
            active INTEGER DEFAULT 1
        );
        INSERT INTO players_corrupt (id, first_name, last_name, display_name, rating, active)
        VALUES (7, 'Grace', 'Hopper', 'Grace Hopper', 1600, 1);
        """
    )
    backup_conn.commit()
    backup_conn.close()

    monkeypatch.setattr(admin_routes, "DB_PATH", str(current_db))
    monkeypatch.setattr(admin_routes, "BACKUP_DIR", str(backup_dir))

    app.testing = True
    client = app.test_client()
    conftest.set_admin_session(client, current_db)

    response = client.post("/admin/backups/restore", data={"name": backup_name})

    assert response.status_code == 302

    restored = sqlite3.connect(current_db)
    tables = {
        row[0]
        for row in restored.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "players" in tables
    assert "players_corrupt" not in tables
    row = restored.execute(
        "SELECT first_name, last_name, display_name, rating FROM players WHERE id = 7"
    ).fetchone()
    assert row == ("Grace", "Hopper", "Grace Hopper", 1600)
    restored.close()


def test_is_valid_sqlite_backup_rejects_partially_corrupted_database(tmp_path):
    db_path = tmp_path / "partially_corrupted.db"

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE players (id INTEGER PRIMARY KEY, display_name TEXT)")
    conn.execute("INSERT INTO players (display_name) VALUES ('Alice')")
    conn.commit()
    conn.close()

    raw = bytearray(db_path.read_bytes())
    raw[107] ^= 0xFF
    db_path.write_bytes(raw)

    assert not admin_routes.is_valid_sqlite_backup(db_path)


def test_get_latest_valid_backup_path_ignores_active_database_file(tmp_path, monkeypatch):
    base_dir = tmp_path / "project"
    data_dir = base_dir / "data"
    data_dir.mkdir(parents=True)
    backup_dir = base_dir / "backups"
    backup_dir.mkdir()

    current_db = data_dir / "acg_ratings.db"
    backup_db = backup_dir / "older_backup.db"

    for path in (current_db, backup_db):
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE players (id INTEGER PRIMARY KEY, display_name TEXT)")
        conn.execute("INSERT INTO players (display_name) VALUES ('Test')")
        conn.commit()
        conn.close()

    import os
    current_ts = (datetime.now().timestamp() + 120,)  # seconds in the future to ensure it looks newest
    os.utime(current_db, (current_ts[0], current_ts[0]))

    monkeypatch.setattr(admin_routes, "BASE_DIR", str(base_dir))
    monkeypatch.setattr(admin_routes, "DB_PATH", str(current_db))
    monkeypatch.setattr(admin_routes, "BACKUP_DIR", str(backup_dir))

    assert admin_routes.get_latest_valid_backup_path() == backup_db


def test_get_latest_valid_backup_path_ignores_non_backup_data_databases(tmp_path, monkeypatch):
    base_dir = tmp_path / "project"
    data_dir = base_dir / "data"
    data_dir.mkdir(parents=True)
    backup_dir = base_dir / "backups"
    backup_dir.mkdir()

    current_db = data_dir / "acg_ratings.db"
    timestamped_backup = backup_dir / "2026-08-13-183034.db"
    debug_data_db = data_dir / "tmp_process_debug.db"

    for path, name in (
        (current_db, "Current"),
        (timestamped_backup, "Backup"),
        (debug_data_db, "Debug"),
    ):
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE players (id INTEGER PRIMARY KEY, display_name TEXT)")
        conn.execute("INSERT INTO players (display_name) VALUES (?)", (name,))
        conn.commit()
        conn.close()

    import os
    newer_ts = datetime.now().timestamp() + 300
    os.utime(debug_data_db, (newer_ts, newer_ts))

    monkeypatch.setattr(admin_routes, "BASE_DIR", str(base_dir))
    monkeypatch.setattr(admin_routes, "DB_PATH", str(current_db))
    monkeypatch.setattr(admin_routes, "BACKUP_DIR", str(backup_dir))

    assert admin_routes.get_latest_valid_backup_path() == timestamped_backup


def test_delete_match_recovers_from_database_error(tmp_path, monkeypatch):
    current_db = tmp_path / "current.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup_path = backup_dir / "valid_backup.db"

    backup_conn = sqlite3.connect(backup_path)
    backup_conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT NOT NULL,
            games_played INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            draws INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL
        );
        INSERT INTO players (id, display_name) VALUES (1, 'Alice');
        INSERT INTO matches (id, match_date, white_player_id, black_player_id, result)
        VALUES (1, '2026-01-01', 1, 2, '1-0');
        """
    )
    backup_conn.commit()
    backup_conn.close()

    conn = sqlite3.connect(current_db)
    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT NOT NULL,
            games_played INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            draws INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL
        );
        INSERT INTO players (id, display_name) VALUES (1, 'Alice'), (2, 'Bob');
        INSERT INTO matches (id, match_date, white_player_id, black_player_id, result)
        VALUES (1, '2026-01-01', 1, 2, '1-0');
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(admin_routes, "DB_PATH", str(current_db))
    monkeypatch.setattr(common, "DB_PATH", str(current_db))
    monkeypatch.setattr(admin_routes, "BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr(admin_routes, "refresh_stats", lambda: (_ for _ in ()).throw(sqlite3.DatabaseError("database disk image is malformed")))
    monkeypatch.setattr(admin_routes, "get_latest_valid_backup_path", lambda: backup_path)

    restore_called = {}
    def fake_restore(path):
        restore_called["path"] = path
        return True
    monkeypatch.setattr(admin_routes, "restore_db_from_backup", fake_restore)

    app.testing = True
    client = app.test_client()
    conftest.set_admin_session(client, current_db)

    response = client.post("/admin/matches/delete?id=1&lang=en")

    assert response.status_code == 302
    assert restore_called["path"] == backup_path


def test_edit_match_recovers_from_database_error(tmp_path, monkeypatch):
    current_db = tmp_path / "current.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup_path = backup_dir / "valid_backup.db"

    backup_conn = sqlite3.connect(backup_path)
    backup_conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT NOT NULL,
            games_played INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            draws INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            event TEXT,
            notes TEXT,
            round_number INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO players (id, display_name) VALUES (1, 'Alice'), (2, 'Bob');
        INSERT INTO matches (id, match_date, white_player_id, black_player_id, result, event, notes, round_number)
        VALUES (1, '2026-01-01', 1, 2, '1-0', '', '', 0);
        """
    )
    backup_conn.commit()
    backup_conn.close()

    conn = sqlite3.connect(current_db)
    conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            display_name TEXT NOT NULL,
            games_played INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            draws INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            event TEXT,
            notes TEXT,
            round_number INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO players (id, display_name) VALUES (1, 'Alice'), (2, 'Bob');
        INSERT INTO matches (id, match_date, white_player_id, black_player_id, result, event, notes, round_number)
        VALUES (1, '2026-01-01', 1, 2, '1-0', '', '', 0);
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(admin_routes, "DB_PATH", str(current_db))
    monkeypatch.setattr(common, "DB_PATH", str(current_db))
    monkeypatch.setattr(admin_routes, "BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr(admin_routes, "refresh_stats", lambda: (_ for _ in ()).throw(sqlite3.DatabaseError("database disk image is malformed")))
    monkeypatch.setattr(admin_routes, "get_latest_valid_backup_path", lambda: backup_path)

    restore_called = {}
    def fake_restore(path):
        restore_called["path"] = path
        return True
    monkeypatch.setattr(admin_routes, "restore_db_from_backup", fake_restore)

    app.testing = True
    client = app.test_client()
    conftest.set_admin_session(client, current_db)

    response = client.post(
        "/admin/matches/edit?id=1&lang=en",
        data={
            "match_date": "2026-01-02",
            "white_player_id": "1",
            "black_player_id": "2",
            "result": "0-1",
            "event": "",
            "notes": "",
        },
    )

    assert response.status_code == 302
    assert restore_called["path"] == backup_path


def test_recalculate_ratings_restores_latest_backup_after_db_error(tmp_path, monkeypatch):
    current_db = tmp_path / "current.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    backup_name = f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.db"
    backup_path = backup_dir / backup_name
    backup_conn = sqlite3.connect(backup_path)
    backup_conn.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            display_name TEXT,
            rating REAL,
            rd REAL,
            volatility REAL,
            initial_rating REAL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL
        );
        CREATE TABLE rating_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            rating REAL NOT NULL,
            rd REAL NOT NULL,
            volatility REAL NOT NULL
        );
        CREATE TABLE rating_config (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            tau REAL,
            default_rating REAL,
            default_rd REAL,
            default_volatility REAL
        );
        CREATE TABLE rating_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            earliest_dirty_date TEXT
        );
        INSERT INTO rating_config (id, tau, default_rating, default_rd, default_volatility)
        VALUES (1, 0.35, 1500.0, 350.0, 0.06);
        INSERT INTO rating_state (id, earliest_dirty_date) VALUES (1, NULL);
        INSERT INTO players (id, first_name, last_name, display_name, rating, rd, volatility, initial_rating, active)
        VALUES (1, 'Alice', 'A', 'Alice', 1500.0, 350.0, 0.06, 1500.0, 1),
               (2, 'Bob', 'B', 'Bob', 1500.0, 350.0, 0.06, 1500.0, 1);
        INSERT INTO matches (match_date, white_player_id, black_player_id, result)
        VALUES ('2026-01-01', 1, 2, '1-0');
        """
    )
    backup_conn.commit()
    backup_conn.close()

    current_db.write_bytes(backup_path.read_bytes())

    monkeypatch.setattr(admin_routes, "DB_PATH", str(current_db))
    monkeypatch.setattr(admin_routes, "BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr(admin_routes, "recompute_ratings", lambda: (_ for _ in ()).throw(sqlite3.DatabaseError("database disk image is malformed")))

    app.testing = True
    client = app.test_client()
    conftest.set_admin_session(client, current_db)

    response = client.post("/admin/ratings", data={"action": "recalculate"})

    assert response.status_code == 302

    restored = sqlite3.connect(current_db)
    try:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 2
        assert restored.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1
        assert restored.execute("SELECT display_name FROM players ORDER BY id").fetchall() == [("Alice",), ("Bob",)]
    finally:
        restored.close()
