"""Main application module for the Glicko rating system web service."""
import logging
import os
import sqlite3
import re
from pathlib import Path

from flask import Flask, request, url_for
from flask_wtf.csrf import CSRFProtect

from config import BASE_DIR, DB_PATH, DEFAULT_RATING, DEFAULT_RD, DEFAULT_VOLATILITY, GLICKO_K, GLICKO_M, TAU

from services.common import (
    TRANSLATIONS,
    bootstrap_default_admin_account,
    get_language,
    get_db,
    get_current_user,
    migrate_audit_log_schema,
    migrate_auth_schema,
    refresh_stats,
    current_timestamp,
)

from services.helpers import normalize_round_note
from services.import_service import import_workbook_data
from services.rating_service import recompute_ratings
from services.settings_service import migrate_application_settings_schema
from routes.public import glicko_to_category, register_public_routes
from routes.admin import register_admin_routes
from services.pairing_service import format_rank_category

csrf = CSRFProtect()


def create_app(test_config=None, auto_init=True):
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

    app_instance = Flask(__name__)
    app_instance.config.update(
        SECRET_KEY=os.environ["APP_SECRET_KEY"],
        SESSION_COOKIE_SECURE=(os.getenv("SESSION_COOKIE_SECURE", "true").lower() in ("true", "1")),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        WTF_CSRF_TIME_LIMIT=None,
    )

    if os.getenv("WTF_CSRF_ENABLED") is not None:
        app_instance.config["WTF_CSRF_ENABLED"] = (
            os.getenv("WTF_CSRF_ENABLED", "true").lower() in ("true", "1")
        )

    if test_config:
        app_instance.config.update(test_config)

    app_instance.secret_key = app_instance.config["SECRET_KEY"]

    @app_instance.before_request
    def configure_csrf_check():
        if (app_instance.testing or app_instance.config.get("TESTING")) and not app_instance.config.get("WTF_CSRF_ENABLED_IN_TESTS", False):
            app_instance.config["WTF_CSRF_CHECK_DEFAULT"] = False
        else:
            app_instance.config["WTF_CSRF_CHECK_DEFAULT"] = True

    csrf.init_app(app_instance)

    @app_instance.context_processor
    def inject_globals():
        def current_lang():
            return get_language(request.args.get("lang"))

        return {
            "current_lang": current_lang,
            "current_user": get_current_user(),
            "glicko_to_category": glicko_to_category,
            "translations": TRANSLATIONS,
            "format_match_result": format_match_result,
            "format_rank_category": format_rank_category,
        }

    @app_instance.url_build_error_handlers.append
    def handle_url_build_error(error: Exception, endpoint: str, values: dict) -> str:
        if "." not in endpoint:
            for prefix in ("public", "admin"):
                target = f"{prefix}.{endpoint}"
                if target in app_instance.view_functions:
                    return url_for(target, **values)
        raise error

    register_public_routes(app_instance)
    register_admin_routes(app_instance)

    if auto_init and not (test_config and test_config.get("SKIP_INIT_DB")):
        initialize_app()

    return app_instance


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            display_name TEXT,
            country TEXT,
            club TEXT,
            slug TEXT UNIQUE,
            active INTEGER DEFAULT 1,
            rating REAL DEFAULT 1500,
            rd REAL DEFAULT 350,
            volatility REAL DEFAULT 0.06,
            games_played INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            draws INTEGER DEFAULT 0,
            last_game_date TEXT
        );

        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL CHECK(result IN ('1-0', '0-1', '1/2-1/2')),
            event TEXT,
            notes TEXT,
            round_number INTEGER NOT NULL DEFAULT 0,
            tournament_pairing_id INTEGER,
            handicap_stones INTEGER NOT NULL DEFAULT 0,
            CHECK (white_player_id != black_player_id),
            FOREIGN KEY (white_player_id) REFERENCES players(id) ON DELETE CASCADE,
            FOREIGN KEY (black_player_id) REFERENCES players(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS rating_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            rating REAL NOT NULL,
            rd REAL NOT NULL,
            volatility REAL NOT NULL,
            FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            short_name TEXT,
            location TEXT,
            description TEXT,
            begin_date TEXT,
            end_date TEXT,
            rounds INTEGER NOT NULL DEFAULT 1,
            tournament_type TEXT NOT NULL DEFAULT 'swiss',
            pairing_system TEXT NOT NULL DEFAULT 'swiss',
            acceleration_scheme TEXT NOT NULL DEFAULT '50:1,25:0.5,25:0',
            bye_points REAL NOT NULL DEFAULT 1,
            absent_points REAL NOT NULL DEFAULT 0,
            handicap_enabled INTEGER NOT NULL DEFAULT 0,
            placement_criteria TEXT NOT NULL DEFAULT 'NBW,SOS,SOSOS',
            mm_bar INTEGER NOT NULL DEFAULT 8,
            mm_floor INTEGER NOT NULL DEFAULT -30,
            mm_zero INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'draft',
            source_format TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS tournament_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            seed_rating REAL NOT NULL DEFAULT 0,
            seed_rank INTEGER NOT NULL DEFAULT 0,
            category TEXT NOT NULL DEFAULT '',
            initial_score REAL NOT NULL DEFAULT 0,
            acceleration REAL NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            received_bye INTEGER NOT NULL DEFAULT 0,
            mc_seeds_calculated INTEGER NOT NULL DEFAULT 0,
            UNIQUE(tournament_id, player_id)
        );

        CREATE TABLE IF NOT EXISTS tournament_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            round_number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled',
            UNIQUE(tournament_id, round_number)
        );

        CREATE TABLE IF NOT EXISTS tournament_round_players (
            round_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('paired', 'bye', 'absent')),
            UNIQUE(round_id, player_id)
        );

        CREATE TABLE IF NOT EXISTS tournament_pairings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER NOT NULL,
            board_number INTEGER NOT NULL DEFAULT 1,
            white_player_id INTEGER,
            black_player_id INTEGER,
            white_player_name TEXT,
            black_player_name TEXT,
            result TEXT,
            is_bye INTEGER NOT NULL DEFAULT 0,
            handicap_stones INTEGER NOT NULL DEFAULT 0,
            UNIQUE(round_id, board_number),
            CHECK (white_player_id != black_player_id),
            FOREIGN KEY (round_id) REFERENCES tournament_rounds(id) ON DELETE CASCADE,
            FOREIGN KEY (white_player_id) REFERENCES players(id) ON DELETE RESTRICT,
            FOREIGN KEY (black_player_id) REFERENCES players(id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS tournament_pending_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            display_name TEXT NOT NULL,
            suggested_name TEXT,
            resolved_player_id INTEGER,
            rating REAL NOT NULL DEFAULT 0,
            rank INTEGER NOT NULL DEFAULT 0,
            category TEXT NOT NULL DEFAULT '',
            source_key TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()
    conn.close()


def format_match_result(result, lang="es", player_id=None, white_player_id=None, black_player_id=None):
    """Format a match result for display, optionally from the perspective of a specific player."""
    translations = TRANSLATIONS.get(lang, TRANSLATIONS["es"])
    normalized = str(result).strip().lower()
    if normalized in {"1-0", "white", "w", "win", "victoria"}:
        if player_id is not None and white_player_id == player_id:
            return translations["result_win"]
        if player_id is not None and black_player_id == player_id:
            return translations["result_loss"]
        return translations["result_win"]
    if normalized in {"0-1", "black", "l", "lose", "loss", "derrota"}:
        if player_id is not None and white_player_id == player_id:
            return translations["result_loss"]
        if player_id is not None and black_player_id == player_id:
            return translations["result_win"]
        return translations["result_loss"]
    return translations["result_draw"]



def refresh_startup_stats(seeded):
    """Refresh per-startup caches after seeding data or explicit refresh requests."""
    if seeded:
        recompute_ratings()
    if seeded or os.getenv("REFRESH_STATS_ON_STARTUP") == "1":
        refresh_stats()


def load_sample_data():
    if os.getenv("LOAD_SAMPLE_DATA") != "1":
        return False

    selected_workbook = Path(BASE_DIR) / "rank-final.xlsx"
    if not selected_workbook.exists():
        return False

    import_workbook_data(selected_workbook, reset=True)
    recompute_ratings()
    refresh_stats()
    return True


def migrate_matches_notes_schema(conn):
    """Keep match note metadata as free-form text and add a numeric sort key."""
    table_info = conn.execute("PRAGMA table_info(matches)").fetchall()
    if not table_info:
        return

    columns = {column[1] for column in table_info}
    if "event" not in columns:
        conn.execute("ALTER TABLE matches ADD COLUMN event TEXT")
    if "notes" not in columns:
        conn.execute("ALTER TABLE matches ADD COLUMN notes TEXT")
    if "round_number" not in columns:
        conn.execute(
            "ALTER TABLE matches ADD COLUMN round_number INTEGER NOT NULL DEFAULT 0"
        )
    conn.commit()

    notes_column = next((column for column in table_info if column[1] == "notes"), None)
    if notes_column is None:
        return
    if notes_column[2].upper() in {"TEXT", "NULL"}:
        return

    legacy_name = "matches__legacy_round_notes"
    conn.execute("ALTER TABLE matches RENAME TO matches__legacy_round_notes")
    conn.execute(
        """
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL CHECK(result IN ('1-0', '0-1', '1/2-1/2')),
            event TEXT,
            notes TEXT,
            round_number INTEGER NOT NULL DEFAULT 0,
            tournament_pairing_id INTEGER,
            handicap_stones INTEGER NOT NULL DEFAULT 0,
            CHECK (white_player_id != black_player_id),
            FOREIGN KEY (white_player_id) REFERENCES players(id) ON DELETE CASCADE,
            FOREIGN KEY (black_player_id) REFERENCES players(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        INSERT INTO matches (id, match_date, white_player_id, black_player_id, result, event, notes, round_number, tournament_pairing_id, handicap_stones)
        SELECT id, match_date, white_player_id, black_player_id, result, event, CAST(notes AS TEXT), 0, NULL, 0
        FROM matches__legacy_round_notes
        """
    )
    conn.execute(f"DROP TABLE {legacy_name}")
    conn.commit()


def migrate_handicap_schema(conn):
    """Add handicap_stones tracking to matches and tournament_pairings.

    Defaults to 0 (no handicap) so every existing row, and every future
    non-handicap match, is unaffected -- rating_service.py treats a
    missing or zero handicap_stones value as a complete no-op.
    """
    for table in ("matches", "tournament_pairings"):
        columns = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if columns and "handicap_stones" not in columns:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN handicap_stones INTEGER NOT NULL DEFAULT 0"
            )
    conn.commit()


def migrate_tournament_match_identity_schema(conn):
    """Give materialized tournament pairings a stable, unique match identity."""
    table_info = conn.execute("PRAGMA table_info(matches)").fetchall()
    if not table_info:
        return
    columns = {row["name"] for row in table_info}
    if "tournament_pairing_id" not in columns:
        conn.execute("ALTER TABLE matches ADD COLUMN tournament_pairing_id INTEGER")
    conn.execute(
        """
        DELETE FROM matches
        WHERE tournament_pairing_id IS NOT NULL
          AND id NOT IN (
              SELECT MIN(id)
              FROM matches
              WHERE tournament_pairing_id IS NOT NULL
              GROUP BY tournament_pairing_id
          )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_tournament_pairing_unique
        ON matches (tournament_pairing_id)
        WHERE tournament_pairing_id IS NOT NULL
        """
    )
    conn.commit()


def normalize_match_round_values(conn):
    """Add and backfill the numeric round key without changing raw note text."""
    table_info = conn.execute("PRAGMA table_info(matches)").fetchall()
    if not table_info:
        return

    columns = {row["name"] for row in table_info}
    if "round_number" not in columns:
        conn.execute(
            "ALTER TABLE matches ADD COLUMN round_number INTEGER NOT NULL DEFAULT 0"
        )

    if "notes" not in columns:
        conn.commit()
        return

    rows = conn.execute("SELECT id, notes, round_number FROM matches").fetchall()
    for row in rows:
        normalized = normalize_round_note(row["notes"])
        if row["round_number"] != normalized:
            conn.execute(
                "UPDATE matches SET round_number = ? WHERE id = ?",
                (normalized, row["id"]),
            )
    conn.commit()


def seed_initial_players():
    """Seed the database with initial player records if no players or matches exist."""
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    match_count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    conn.close()

    if count > 0 or match_count > 0:
        return False

    players = [
        ("Juan", "Samper", "Juan Samper", "COL", "Club A", "juan-samper"),
        ("Camilo", "Acuna", "Camilo Acuna", "COL", "Club B", "camilo-acuna"),
        ("Fabio", "Moreno", "Fabio Moreno", "COL", "Club A", "fabio-moreno"),
        ("Jaime", "Ramirez", "Jaime Ramirez", "COL", "Club C", "jaime-ramirez"),
        ("Sofia", "Lopez", "Sofia Lopez", "COL", "Club B", "sofia-lopez"),
    ]
    conn = get_db()
    for first_name, last_name, display_name, country, club, slug in players:
        conn.execute(
            "INSERT INTO players (first_name, last_name, display_name, country, club, slug, rating, rd, volatility) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (first_name, last_name, display_name, country, club, slug, DEFAULT_RATING, DEFAULT_RD, DEFAULT_VOLATILITY),
        )
    conn.commit()

    player_rows = conn.execute("SELECT id, slug FROM players ORDER BY id").fetchall()
    player_map = {row["slug"]: row["id"] for row in player_rows}
    sample_matches = [
        ("2026-06-01", player_map["juan-samper"], player_map["camilo-acuna"], "1-0", "Round 1", 1, 1),
        ("2026-06-02", player_map["fabio-moreno"], player_map["jaime-ramirez"], "1-0", "Round 2", 2, 2),
        ("2026-06-03", player_map["juan-samper"], player_map["fabio-moreno"], "0-1", "Round 3", 3, 3),
        ("2026-06-04", player_map["camilo-acuna"], player_map["jaime-ramirez"], "1/2-1/2", "Round 4", 4, 4),
        ("2026-06-05", player_map["sofia-lopez"], player_map["juan-samper"], "0-1", "Round 5", 5, 5),
    ]
    conn.executemany(
        "INSERT INTO matches (match_date, white_player_id, black_player_id, result, event, notes, round_number) VALUES (?, ?, ?, ?, ?, ?, ?)",
        sample_matches,
    )
    conn.commit()
    conn.close()
    recompute_ratings()
    return True


def seed_data():
    if os.getenv("LOAD_SAMPLE_DATA") == "1":
        return load_sample_data()
    return seed_initial_players()


def migrate_config_schema(conn):
    """Ensure config tables include the default row and audit metadata used by admin settings."""
    tables = {
        "rating_config": (
            """
            CREATE TABLE IF NOT EXISTS rating_config (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                tau REAL,
                default_rating REAL,
                default_rd REAL,
                default_volatility REAL,
                updated_at TEXT
            )
            """,
            (
                "INSERT OR IGNORE INTO rating_config (id, tau, default_rating, default_rd, default_volatility, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (1, TAU, DEFAULT_RATING, DEFAULT_RD, DEFAULT_VOLATILITY, current_timestamp()),
            ),
        ),
        "category_config": (
            """
            CREATE TABLE IF NOT EXISTS category_config (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                glicko_k REAL,
                glicko_m REAL,
                updated_at TEXT
            )
            """,
            (
                "INSERT OR IGNORE INTO category_config (id, glicko_k, glicko_m, updated_at) VALUES (?, ?, ?, ?)",
                (1, GLICKO_K, GLICKO_M, current_timestamp()),
            ),
        ),
    }

    for table, (create_sql, seed_sql) in tables.items():
        conn.execute(create_sql)
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "updated_at" not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN updated_at TEXT")
        if conn.execute(f"SELECT COUNT(*) FROM {table} WHERE id = 1").fetchone()[0] == 0:
            conn.execute(seed_sql[0], seed_sql[1])

    conn.commit()


def migrate_tournament_schema(conn):
    """Create or upgrade tournament tables before any tournament queries run."""
    schema_version = 5
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if current_version >= schema_version:
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '', short_name TEXT, location TEXT, description TEXT,
            begin_date TEXT, end_date TEXT, rounds INTEGER NOT NULL DEFAULT 1,
            tournament_type TEXT NOT NULL DEFAULT 'swiss',
            pairing_system TEXT NOT NULL DEFAULT 'swiss',
            acceleration_scheme TEXT NOT NULL DEFAULT '50:1,25:0.5,25:0',
            bye_points REAL NOT NULL DEFAULT 1,
            absent_points REAL NOT NULL DEFAULT 0,
            handicap_enabled INTEGER NOT NULL DEFAULT 0,
            placement_criteria TEXT NOT NULL DEFAULT 'NBW,SOS,SOSOS',
            mm_bar INTEGER NOT NULL DEFAULT 8,
            mm_floor INTEGER NOT NULL DEFAULT -30,
            mm_zero INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'draft', source_format TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS tournament_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL, player_id INTEGER NOT NULL,
            seed_rating REAL NOT NULL DEFAULT 0, seed_rank INTEGER NOT NULL DEFAULT 0,
            category TEXT NOT NULL DEFAULT '',
            initial_score REAL NOT NULL DEFAULT 0, acceleration REAL NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0, received_bye INTEGER NOT NULL DEFAULT 0,
            mc_seeds_calculated INTEGER NOT NULL DEFAULT 0,
            UNIQUE(tournament_id, player_id)
        );
        CREATE TABLE IF NOT EXISTS tournament_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL, round_number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled',
            UNIQUE(tournament_id, round_number)
        );
        CREATE TABLE IF NOT EXISTS tournament_round_players (
            round_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('paired', 'bye', 'absent')),
            UNIQUE(round_id, player_id)
        );
        CREATE TABLE IF NOT EXISTS tournament_pairings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER NOT NULL, board_number INTEGER NOT NULL DEFAULT 1,
            white_player_id INTEGER, black_player_id INTEGER,
            white_player_name TEXT, black_player_name TEXT,
            result TEXT, is_bye INTEGER NOT NULL DEFAULT 0,
            UNIQUE(round_id, board_number)
        );
        CREATE TABLE IF NOT EXISTS tournament_pending_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            display_name TEXT NOT NULL,
            suggested_name TEXT,
            resolved_player_id INTEGER,
            rating REAL NOT NULL DEFAULT 0,
            rank INTEGER NOT NULL DEFAULT 0,
            category TEXT NOT NULL DEFAULT '',
            source_key TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    required_columns = {
        "tournaments": {
            "name": "TEXT NOT NULL DEFAULT ''", "short_name": "TEXT",
            "location": "TEXT", "description": "TEXT", "begin_date": "TEXT", "end_date": "TEXT",
            "rounds": "INTEGER NOT NULL DEFAULT 1",
            "tournament_type": "TEXT NOT NULL DEFAULT 'swiss'",
            "pairing_system": "TEXT NOT NULL DEFAULT 'swiss'",
            "acceleration_scheme": "TEXT NOT NULL DEFAULT '50:1,25:0.5,25:0'",
            "acceleration_rounds": "INTEGER NOT NULL DEFAULT 2",
            "category_rounds": "INTEGER NOT NULL DEFAULT 0",
            "bye_points": "REAL NOT NULL DEFAULT 1",
            "absent_points": "REAL NOT NULL DEFAULT 0",
            "handicap_enabled": "INTEGER NOT NULL DEFAULT 0",
            "placement_criteria": "TEXT NOT NULL DEFAULT 'NBW,SOS,SOSOS'",
            "mm_bar": "INTEGER NOT NULL DEFAULT 8",
            "mm_floor": "INTEGER NOT NULL DEFAULT -30",
            "mm_zero": "INTEGER NOT NULL DEFAULT 0",
            "status": "TEXT NOT NULL DEFAULT 'draft'", "source_format": "TEXT",
            "created_at": "TEXT",
        },
        "tournament_participants": {
            "tournament_id": "INTEGER NOT NULL DEFAULT 0",
            "player_id": "INTEGER NOT NULL DEFAULT 0",
            "seed_rating": "REAL NOT NULL DEFAULT 0", "seed_rank": "INTEGER NOT NULL DEFAULT 0",
            "category": "TEXT NOT NULL DEFAULT ''",
            "initial_score": "REAL NOT NULL DEFAULT 0", "acceleration": "REAL NOT NULL DEFAULT 0",
            "score": "REAL NOT NULL DEFAULT 0", "received_bye": "INTEGER NOT NULL DEFAULT 0",
            "mc_seeds_calculated": "INTEGER NOT NULL DEFAULT 0",
        },
        "tournament_rounds": {
            "tournament_id": "INTEGER NOT NULL DEFAULT 0",
            "round_number": "INTEGER NOT NULL DEFAULT 0",
            "status": "TEXT NOT NULL DEFAULT 'scheduled'",
        },
        "tournament_pairings": {
            "round_id": "INTEGER NOT NULL DEFAULT 0",
            "white_player_id": "INTEGER",
            "board_number": "INTEGER NOT NULL DEFAULT 1", "black_player_id": "INTEGER",
            "white_player_name": "TEXT",
            "black_player_name": "TEXT",
            "result": "TEXT", "is_bye": "INTEGER NOT NULL DEFAULT 0",
            "handicap_stones": "INTEGER NOT NULL DEFAULT 0",
        },
        "tournament_pending_players": {
            "tournament_id": "INTEGER NOT NULL DEFAULT 0",
            "display_name": "TEXT NOT NULL DEFAULT ''",
            "suggested_name": "TEXT",
            "resolved_player_id": "INTEGER",
            "rating": "REAL NOT NULL DEFAULT 0",
            "rank": "INTEGER NOT NULL DEFAULT 0",
            "category": "TEXT NOT NULL DEFAULT ''",
            "source_key": "TEXT",
            "created_at": "TEXT",
        },
    }
    try:
        pairing_columns = {
            row[1]: row[3] for row in conn.execute("PRAGMA table_info(tournament_pairings)").fetchall()
        }
        if "white_player_id" in pairing_columns and pairing_columns["white_player_id"] == 1:
            conn.execute("ALTER TABLE tournament_pairings RENAME TO tournament_pairings_legacy")
            conn.execute(
                """
                CREATE TABLE tournament_pairings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    round_id INTEGER NOT NULL,
                    board_number INTEGER NOT NULL DEFAULT 1,
                    white_player_id INTEGER,
                    black_player_id INTEGER,
                    white_player_name TEXT,
                    black_player_name TEXT,
                    result TEXT,
                    is_bye INTEGER NOT NULL DEFAULT 0,
                    handicap_stones INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(round_id, board_number),
                    CHECK (white_player_id != black_player_id),
                    FOREIGN KEY (round_id) REFERENCES tournament_rounds(id) ON DELETE CASCADE,
                    FOREIGN KEY (white_player_id) REFERENCES players(id) ON DELETE RESTRICT,
                    FOREIGN KEY (black_player_id) REFERENCES players(id) ON DELETE RESTRICT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO tournament_pairings
                    (id, round_id, board_number, white_player_id, black_player_id, white_player_name, black_player_name, result, is_bye)
                SELECT id, round_id, board_number, white_player_id, black_player_id, NULL, NULL, result, is_bye
                FROM tournament_pairings_legacy
                """
            )
            conn.execute("DROP TABLE tournament_pairings_legacy")

        for table, columns in required_columns.items():
            existing = {
                row[1]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for column, definition in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        conn.execute(
            """
            UPDATE tournaments
            SET rounds = CASE
                WHEN CAST(rounds AS INTEGER) < 1 THEN 1
                ELSE CAST(rounds AS INTEGER)
            END,
                tournament_type = CASE
                    WHEN pairing_system = 'mcmahon' THEN 'mcmahon'
                    ELSE 'swiss'
                END
            WHERE tournament_type IS NULL OR tournament_type = '' OR CAST(rounds AS INTEGER) < 1
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tournament_round_players_round_status
            ON tournament_round_players (round_id, status)
            """
        )
        conn.execute(f"PRAGMA user_version = {schema_version}")
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise RuntimeError(f"Tournament schema migration failed: {exc}") from exc


def _tables_referencing_legacy_players(conn):
    """Identify tables that have foreign key references to the legacy `players_corrupt` table."""
    rows = conn.execute(
        """
        SELECT name, sql
        FROM sqlite_master
        WHERE type = 'table' AND sql IS NOT NULL
        """
    ).fetchall()
    pattern = re.compile(
        r"REFERENCES\s+([\"'`]?)(players_corrupt)\1\s*\(",
        re.IGNORECASE,
    )
    references = []
    for row in rows:
        name = row[0]
        create_sql = row[1] or ""
        if name in {"players", "players_corrupt"}:
            continue
        if pattern.search(create_sql):
            references.append((name, create_sql))
    return references


def _rebuild_table_without_legacy_players_fk(conn, table_name, create_sql):
    """Rebuild a table without foreign key references to the legacy `players_corrupt` table."""
    legacy_name = f"{table_name}__legacy_fk_repair"
    conn.execute(f'ALTER TABLE "{table_name}" RENAME TO "{legacy_name}"')

    repaired_sql = re.sub(
        r"REFERENCES\s+([\"'`]?)(players_corrupt)\1\s*\(",
        "REFERENCES players(",
        create_sql,
        flags=re.IGNORECASE,
    )
    conn.execute(repaired_sql)

    source_columns = {
        row[1] for row in conn.execute(f'PRAGMA table_info("{legacy_name}")').fetchall()
    }
    target_columns = [
        row[1] for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    ]
    shared_columns = [column for column in target_columns if column in source_columns]

    if shared_columns:
        columns_sql = ", ".join(f'"{column}"' for column in shared_columns)
        conn.execute(
            f'INSERT INTO "{table_name}" ({columns_sql}) '
            f'SELECT {columns_sql} FROM "{legacy_name}"'
        )

    conn.execute(f'DROP TABLE "{legacy_name}"')


def repair_legacy_players_table(conn):
    """Restore a legacy database whose `players` table was renamed during debugging.

    Older ad hoc repair scripts sometimes renamed the active `players` table to
    `players_corrupt` while rebuilding the schema. The app should detect that
    condition and restore the canonical `players` table before running any query
    against player data.
    """
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }

    has_corrupt_table = "players_corrupt" in tables
    # A prior repair run may have already dropped players_corrupt without
    # rebinding child FKs, so this must be detected even without the table.
    referencing_tables = _tables_referencing_legacy_players(conn)

    if not has_corrupt_table and not referencing_tables:
        return

    conn.execute("DROP TRIGGER IF EXISTS players_fts_ai")
    conn.execute("DROP TRIGGER IF EXISTS players_fts_ad")
    conn.execute("DROP TRIGGER IF EXISTS players_fts_au")
    conn.execute("DROP TABLE IF EXISTS players_fts")

    if has_corrupt_table:
        if "players" not in tables:
            conn.execute("ALTER TABLE players_corrupt RENAME TO players")
        else:
            source_cols = [
                row[1]
                for row in conn.execute("PRAGMA table_info(players_corrupt)").fetchall()
            ]
            target_cols = [
                row[1]
                for row in conn.execute("PRAGMA table_info(players)").fetchall()
            ]
            shared_cols = [col for col in source_cols if col in target_cols]
            if shared_cols:
                columns_sql = ", ".join(shared_cols)
                conn.execute(
                    f"INSERT OR IGNORE INTO players ({columns_sql}) "
                    f"SELECT {columns_sql} FROM players_corrupt"
                )

    if "players" in tables or has_corrupt_table:
        for table_name, create_sql in referencing_tables:
            _rebuild_table_without_legacy_players_fk(conn, table_name, create_sql)

    conn.execute("DROP TABLE IF EXISTS players_corrupt")

    player_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(players)").fetchall()
    }
    if "initial_rating" not in player_columns:
        conn.execute("ALTER TABLE players ADD COLUMN initial_rating REAL")

    conn.commit()


def ensure_player_schema_columns(conn):
    """Ensure the working player schema includes the stats columns needed by admin and public views."""
    if "players" not in {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }:
        return

    player_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(players)").fetchall()
    }

    column_definitions = {
        "first_name": "TEXT",
        "last_name": "TEXT",
        "display_name": "TEXT",
        "country": "TEXT",
        "club": "TEXT",
        "slug": "TEXT",
        "active": "INTEGER DEFAULT 1",
        "rating": "REAL DEFAULT 1500",
        "rd": "REAL DEFAULT 350",
        "volatility": "REAL DEFAULT 0.06",
        "games_played": "INTEGER DEFAULT 0",
        "wins": "INTEGER DEFAULT 0",
        "losses": "INTEGER DEFAULT 0",
        "draws": "INTEGER DEFAULT 0",
        "last_game_date": "TEXT",
        "initial_rating": "REAL",
    }

    for column_name, definition in column_definitions.items():
        if column_name not in player_columns:
            conn.execute(f"ALTER TABLE players ADD COLUMN {column_name} {definition}")

    conn.commit()


def initialize_app():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    init_db()

    conn = get_db()
    repair_legacy_players_table(conn)

    #migrations

    migrate_tournament_schema(conn)
    migrate_config_schema(conn)
    migrate_auth_schema(conn)
    migrate_application_settings_schema(conn)
    migrate_audit_log_schema(conn)
    bootstrap_default_admin_account(conn)
    ensure_player_schema_columns(conn)
    migrate_matches_notes_schema(conn)
    migrate_tournament_match_identity_schema(conn)
    normalize_match_round_values(conn)
    migrate_handicap_schema(conn)

    columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(players)"
        ).fetchall()
    }

    if "initial_rating" not in columns:
        conn.execute(
            """
            ALTER TABLE players
            ADD COLUMN initial_rating REAL
            """
        )

    conn.execute(
        """
        UPDATE players
        SET rating = CASE
                WHEN rating IS NULL OR rating <= 0 THEN ?
                WHEN rating < ? THEN ?
                ELSE rating
            END,
            initial_rating = CASE
                WHEN initial_rating IS NULL OR initial_rating <= 0 THEN ?
                WHEN initial_rating < ? THEN ?
                ELSE initial_rating
            END
        WHERE rating IS NULL OR rating < ?
           OR initial_rating IS NULL OR initial_rating < ?
        """,
        (
            DEFAULT_RATING,
            GLICKO_M,
            GLICKO_M,
            DEFAULT_RATING,
            GLICKO_M,
            GLICKO_M,
            GLICKO_M,
            GLICKO_M,
        ),
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_matches_white_player_date
        ON matches (white_player_id, match_date, id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_matches_black_player_date
        ON matches (black_player_id, match_date, id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_matches_date_round_id
        ON matches (match_date DESC, round_number ASC, id DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rating_snapshots_player_date
        ON rating_snapshots (player_id, snapshot_date, id)
        """
    )

    # Rebuild FTS artifacts so trigger definitions stay in sync across upgrades.
    conn.execute("DROP TRIGGER IF EXISTS players_fts_ai")
    conn.execute("DROP TRIGGER IF EXISTS players_fts_ad")
    conn.execute("DROP TRIGGER IF EXISTS players_fts_au")
    conn.execute("DROP TABLE IF EXISTS players_fts")

    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS players_fts USING fts5(
            id UNINDEXED,
            display_name,
            country,
            club,
            slug,
            content='players',
            content_rowid='id'
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS players_fts_ai AFTER INSERT ON players BEGIN
            INSERT INTO players_fts(rowid, id, display_name, country, club, slug)
            VALUES (new.id, new.id, new.display_name, new.country, new.club, new.slug);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS players_fts_ad AFTER DELETE ON players BEGIN
            INSERT INTO players_fts(players_fts, rowid, id, display_name, country, club, slug)
            VALUES('delete', old.id, old.id, old.display_name, old.country, old.club, old.slug);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS players_fts_au AFTER UPDATE ON players BEGIN
            INSERT INTO players_fts(players_fts, rowid, id, display_name, country, club, slug)
            VALUES('delete', old.id, old.id, old.display_name, old.country, old.club, old.slug);
            INSERT INTO players_fts(rowid, id, display_name, country, club, slug)
            VALUES (new.id, new.id, new.display_name, new.country, new.club, new.slug);
        END
        """
    )
    conn.execute(
        """
        INSERT INTO players_fts(players_fts)
        VALUES('rebuild')
        """
    )

    # Search and filter performance indexes for public listings and admin views.
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_players_active_rating
        ON players (active, rating)
        """
    )
    if "category" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_players_category
            ON players (category)
            """
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_players_country
        ON players (country)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_players_city
        ON players (club)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_players_display_name
        ON players (display_name)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_players_last_game_date
        ON players (last_game_date)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tournament_participants_tournament_player
        ON tournament_participants (tournament_id, player_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tournament_pairings_round
        ON tournament_pairings (round_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tournament_round_players_round_player
        ON tournament_round_players (round_id, player_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tournament_round_players_round_status
        ON tournament_round_players (round_id, status)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rating_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            earliest_dirty_date TEXT
        )
        """
    )

    conn.execute(
        """
        INSERT OR IGNORE INTO rating_state (
            id,
            earliest_dirty_date
        )
        VALUES (
            1,
            NULL
        )
        """
    )

    conn.commit()
    conn.close()
    #end migrations

    seeded = seed_data()
    refresh_startup_stats(seeded)


app = create_app()


### ------------------------------------ REMOVE BELOW FROM PROD ---------------------------------------------- 

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
