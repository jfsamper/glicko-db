import sqlite3

import pytest

import services.category_service as category_service
import services.rating_service as rating_service


def test_category_config_tracks_updated_at(monkeypatch, tmp_path):
    db_path = tmp_path / "category_config.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE category_config (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            glicko_k REAL,
            glicko_m REAL,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO category_config (id, glicko_k, glicko_m, updated_at) VALUES (?, ?, ?, ?)",
        (1, 30.0, 500.0, "2024-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    def factory():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(category_service, "get_db", factory)

    category_service.update_category_config(20.0, 400.0)
    config = category_service.get_category_config()

    assert config["glicko_k"] == 20.0
    assert config["glicko_m"] == 400.0
    assert config["updated_at"] is not None
    assert config["updated_at"] != "2024-01-01T00:00:00"


def test_rating_config_tracks_updated_at(monkeypatch, tmp_path):
    db_path = tmp_path / "rating_config.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE rating_config (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            tau REAL,
            default_rating REAL,
            default_rd REAL,
            default_volatility REAL,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO rating_config (id, tau, default_rating, default_rd, default_volatility, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (1, 0.5, 1500.0, 350.0, 0.06, "2024-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    def factory():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(rating_service, "get_db", factory)

    rating_service.update_rating_config(0.75, 1600.0, 300.0, 0.04)
    config = rating_service.get_rating_config()

    assert config["tau"] == 0.75
    assert config["default_rating"] == 1600.0
    assert config["default_rd"] == 300.0
    assert config["default_volatility"] == 0.04
    assert config["updated_at"] is not None
    assert config["updated_at"] != "2024-01-01T00:00:00"


def test_glicko_to_category_uses_the_persisted_configuration(monkeypatch):
    monkeypatch.setattr(
        category_service,
        "get_category_config",
        lambda: {"glicko_k": 30.0, "glicko_m": 500.0},
    )

    assert category_service.glicko_to_category(1000) == "9 kyu"
    assert category_service.glicko_to_category(1000, k=16.6, m=338) == "11 kyu"


def test_glicko_to_category_handles_invalid_imported_ratings():
    assert category_service.glicko_to_category(0, k=16.6, m=338) == category_service.glicko_to_category(1500, k=16.6, m=338)
    assert category_service.glicko_to_category(None, k=16.6, m=338) == category_service.glicko_to_category(1500, k=16.6, m=338)
    assert rating_service.glicko_to_category(0, k=16.6, m=338) == rating_service.glicko_to_category(1500, k=16.6, m=338)
    assert rating_service.glicko_to_category(1000, k=16.6, m=338) == "11 kyu"


@pytest.mark.parametrize(
    "invalid_k, invalid_m",
    [
        (0, 400.0),
        (-1, 400.0),
        (16.6, 0),
        (16.6, -1),
        ("not-a-number", 400.0),
    ],
)
def test_category_config_rejects_invalid_values(monkeypatch, tmp_path, invalid_k, invalid_m):
    db_path = tmp_path / "category_config_invalid.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE category_config (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            glicko_k REAL,
            glicko_m REAL,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO category_config (id, glicko_k, glicko_m, updated_at) VALUES (?, ?, ?, ?)",
        (1, 30.0, 500.0, "2024-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    def factory():
        scoped_conn = sqlite3.connect(db_path)
        scoped_conn.row_factory = sqlite3.Row
        return scoped_conn

    monkeypatch.setattr(category_service, "get_db", factory)

    with pytest.raises(ValueError):
        category_service.update_category_config(invalid_k, invalid_m)

    config = category_service.get_category_config()
    assert config["glicko_k"] == 30.0
    assert config["glicko_m"] == 500.0
