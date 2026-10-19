from io import BytesIO
import math

import openpyxl
import pytest

import conftest
import routes.admin as admin_routes
import services.common as common
from services.category_service import get_category_config


def test_player_csv_import_previews_then_creates_players(app, client, tmp_path, monkeypatch):
    monkeypatch.setattr(admin_routes, "BASE_DIR", str(tmp_path))
    _, csrf_token = conftest.set_admin_session(client)

    response = client.get("/admin/players?lang=en")
    assert response.status_code == 200
    assert "Import players" in response.get_data(as_text=True)

    response = client.post(
        "/admin/players/import?lang=en",
        data={
            "csrf_token": csrf_token,
            "file": (
                BytesIO(
                    b"Name,Rank,Country\n"
                    b"Alex Import,1d,COL\n"
                    b"Sam Import,5k,US\n"
                ),
                "players.csv",
            ),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "2 ready to import; 0 with errors." in body
    assert "Alex Import" in body
    assert 'name="action" value="commit"' in body

    with client.session_transaction() as session:
        preview_token = session["player_import_preview"]["token"]
    response = client.post(
        "/admin/players/import?lang=en",
        data={
            "csrf_token": csrf_token,
            "action": "commit",
            "preview_token": preview_token,
        },
    )
    assert response.status_code == 302

    conn = common.get_db()
    players = conn.execute(
        """
        SELECT first_name, last_name, country, initial_rating, rating
        FROM players
        WHERE first_name IN ('Alex', 'Sam') AND last_name = 'Import'
        ORDER BY first_name
        """
    ).fetchall()
    conn.close()
    assert [(row["first_name"], row["country"]) for row in players] == [
        ("Alex", "COL"),
        ("Sam", "USA"),
    ]
    assert all(row["initial_rating"] == row["rating"] for row in players)
    category_config = get_category_config()
    assert players[0]["rating"] == pytest.approx(
        category_config["glicko_m"] * math.exp(
            29 / category_config["glicko_k"]
        )
    )


def test_player_xlsx_import_blocks_invalid_rows(app, client, tmp_path, monkeypatch):
    monkeypatch.setattr(admin_routes, "BASE_DIR", str(tmp_path))
    _, csrf_token = conftest.set_admin_session(client)
    workbook_bytes = BytesIO()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Name", "Rank", "Country"])
    sheet.append(["Taylor Import", "2d", "US"])
    sheet.append(["Taylor Import", "bad-rank", "unknown-country"])
    workbook.save(workbook_bytes)
    workbook_bytes.seek(0)

    response = client.post(
        "/admin/players/import?lang=en",
        data={
            "csrf_token": csrf_token,
            "file": (workbook_bytes, "players.xlsx"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "1 ready to import; 1 with errors." in body
    assert "Invalid rank." in body
    assert "The country is invalid." in body
    assert "This name occurs more than once in the file." in body
    assert 'name="action" value="commit"' not in body

    with client.session_transaction() as session:
        preview_token = session["player_import_preview"]["token"]
    response = client.post(
        "/admin/players/import?lang=en",
        data={
            "csrf_token": csrf_token,
            "action": "commit",
            "preview_token": preview_token,
        },
    )
    assert response.status_code == 200
    assert "1 ready to import; 1 with errors." in response.get_data(as_text=True)

    conn = common.get_db()
    assert conn.execute(
        "SELECT COUNT(*) FROM players WHERE first_name = 'Taylor'"
    ).fetchone()[0] == 0
    conn.close()

    workbook_bytes = BytesIO()
    workbook = openpyxl.Workbook()
    workbook.active.append(["Name", "Rank", "Country"])
    workbook.active.append(["Taylor Excel", "2d", "US"])
    workbook.save(workbook_bytes)
    workbook_bytes.seek(0)
    response = client.post(
        "/admin/players/import?lang=en",
        data={
            "csrf_token": csrf_token,
            "file": (workbook_bytes, "valid-players.xlsx"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    with client.session_transaction() as session:
        preview_token = session["player_import_preview"]["token"]
    response = client.post(
        "/admin/players/import?lang=en",
        data={
            "csrf_token": csrf_token,
            "action": "commit",
            "preview_token": preview_token,
        },
    )
    assert response.status_code == 302
    conn = common.get_db()
    assert conn.execute(
        """
        SELECT country FROM players
        WHERE first_name = 'Taylor' AND last_name = 'Excel'
        """
    ).fetchone()[0] == "USA"
    conn.close()


def test_player_import_reports_corrupt_workbooks(app, client, tmp_path, monkeypatch):
    monkeypatch.setattr(admin_routes, "BASE_DIR", str(tmp_path))
    _, csrf_token = conftest.set_admin_session(client)

    response = client.post(
        "/admin/players/import?lang=en",
        data={
            "csrf_token": csrf_token,
            "file": (BytesIO(b"not an xlsx workbook"), "corrupt.xlsx"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert "The file could not be read." in response.get_data(as_text=True)
