import sqlite3

import pytest

from scripts.set_display_names import apply_display_names, build_display_name, main


PLAYERS = [
    (1, "John", "Smith", "old one"),
    (2, "Ana", "Lopez", None),
    (3, "Solo", "", "solo old"),
    (4, "", "", "blank old"),
]


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE players (id INTEGER PRIMARY KEY, first_name TEXT NOT NULL, "
        "last_name TEXT NOT NULL, display_name TEXT)"
    )
    conn.executemany("INSERT INTO players VALUES (?, ?, ?, ?)", PLAYERS)
    conn.commit()
    conn.close()
    return path


def read_display_names(path):
    conn = sqlite3.connect(path)
    try:
        return dict(conn.execute("SELECT id, display_name FROM players ORDER BY id").fetchall())
    finally:
        conn.close()


def test_build_first_last_and_last_first():
    assert build_display_name("John", "Smith", "first-last") == "John Smith"
    assert build_display_name("John", "Smith", "last-first") == "Smith, John"


def test_build_skips_empty_name_parts_and_strips_whitespace():
    assert build_display_name("  John ", " Smith  ", "first-last") == "John Smith"
    assert build_display_name("Solo", "", "last-first") == "Solo"
    assert build_display_name("", "", "first-last") == ""


def test_build_custom_template_with_placeholders():
    assert build_display_name("John", "Smith", "custom", "{last_name} ({first_name})") == "Smith (John)"


def test_build_custom_literal_text_is_used_for_everyone():
    assert build_display_name("John", "Smith", "custom", "Anonymous") == "Anonymous"
    assert build_display_name("Ana", "Lopez", "custom", "Anonymous") == "Anonymous"


def test_apply_updates_all_players_and_reports_stats(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        stats, changes = apply_display_names(conn, "last-first")
    finally:
        conn.close()

    assert stats == {"total": 4, "updated": 3, "unchanged": 0, "skipped": 1}
    assert (1, "old one", "Smith, John") in changes
    assert read_display_names(db_path) == {
        1: "Smith, John",
        2: "Lopez, Ana",
        3: "Solo",
        4: "blank old",
    }


def test_dry_run_does_not_write(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        stats, changes = apply_display_names(conn, "first-last", dry_run=True)
    finally:
        conn.close()

    assert stats["updated"] == 3
    assert len(changes) == 3
    assert read_display_names(db_path)[1] == "old one"


def test_second_run_is_idempotent(db_path):
    assert main(["--format", "first-last", "--db", str(db_path)]) == 0
    first_pass = read_display_names(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        stats, changes = apply_display_names(conn, "first-last")
    finally:
        conn.close()

    assert changes == []
    assert stats["unchanged"] == 3
    assert read_display_names(db_path) == first_pass


def test_cli_custom_template_writes_names(db_path):
    assert main(["--format", "custom", "--template", "{last_name} ({first_name})",
                 "--db", str(db_path)]) == 0
    assert read_display_names(db_path)[2] == "Lopez (Ana)"


def test_cli_requires_template_for_custom_format(db_path):
    with pytest.raises(SystemExit):
        main(["--format", "custom", "--db", str(db_path)])


def test_cli_rejects_template_without_custom_format(db_path):
    with pytest.raises(SystemExit):
        main(["--format", "first-last", "--template", "X", "--db", str(db_path)])


def test_cli_rejects_missing_database(tmp_path):
    with pytest.raises(SystemExit):
        main(["--format", "first-last", "--db", str(tmp_path / "missing.db")])
