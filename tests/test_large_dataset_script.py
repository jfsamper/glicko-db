import sqlite3

from scripts.dev_only import create_large_test_dataset as dataset


def connect_database(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def test_large_dataset_dry_run_does_not_create_database(tmp_path, capsys):
    output_path = tmp_path / "dry-run.db"

    assert dataset.main(
        [
            "--dry-run",
            "--output",
            str(output_path),
            "--users",
            "3",
            "--players",
            "8",
            "--matches",
            "12",
            "--tournaments",
            "2",
        ]
    ) == 0

    assert not output_path.exists()
    assert "3 users" in capsys.readouterr().out


def test_large_dataset_populates_related_tables_and_all_roles(tmp_path):
    output_path = tmp_path / "generated.db"

    assert dataset.main(
        [
            "--output",
            str(output_path),
            "--seed",
            "17",
            "--users",
            "6",
            "--players",
            "20",
            "--matches",
            "80",
            "--tournaments",
            "3",
        ]
    ) == 0

    conn = connect_database(output_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM users WHERE username LIKE 'demo-data-%'").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM players WHERE slug LIKE 'demo-data-%'").fetchone()[0] == 20
        assert conn.execute("SELECT COUNT(*) FROM matches WHERE event LIKE 'DEMO-DATA-%'").fetchone()[0] == 80
        assert conn.execute("SELECT COUNT(*) FROM rating_snapshots").fetchone()[0] == 60
        assert conn.execute("SELECT COUNT(*) FROM tournaments WHERE name LIKE 'DEMO-DATA-%'").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM tournament_participants").fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM matches WHERE white_player_id = black_player_id").fetchone()[0] == 0
        assert {row[0] for row in conn.execute("SELECT name FROM roles").fetchall()} == set(dataset.ROLES)
        assert conn.execute("SELECT COUNT(DISTINCT country) FROM players WHERE slug LIKE 'demo-data-%'").fetchone()[0] > 1
        assert conn.execute("SELECT COUNT(DISTINCT result) FROM matches WHERE event LIKE 'DEMO-DATA-%'").fetchone()[0] == 3
    finally:
        conn.close()


def test_large_dataset_replaces_only_its_own_rows_on_repeat_run(tmp_path):
    output_path = tmp_path / "repeatable.db"

    arguments = [
        "--output",
        str(output_path),
        "--seed",
        "23",
        "--users",
        "3",
        "--players",
        "10",
        "--matches",
        "25",
        "--tournaments",
        "2",
    ]
    dataset.main(arguments)
    first_conn = connect_database(output_path)
    try:
        first_player_names = [row[0] for row in first_conn.execute(
            "SELECT display_name FROM players WHERE slug LIKE 'demo-data-%' ORDER BY slug"
        ).fetchall()]
    finally:
        first_conn.close()

    dataset.main(arguments)
    conn = connect_database(output_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM users WHERE username LIKE 'demo-data-%'").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM players WHERE slug LIKE 'demo-data-%'").fetchone()[0] == 10
        assert conn.execute("SELECT COUNT(*) FROM matches WHERE event LIKE 'DEMO-DATA-%'").fetchone()[0] == 25
        second_player_names = [row[0] for row in conn.execute(
            "SELECT display_name FROM players WHERE slug LIKE 'demo-data-%' ORDER BY slug"
        ).fetchall()]
        assert second_player_names == first_player_names
    finally:
        conn.close()