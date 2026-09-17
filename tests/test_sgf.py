from io import BytesIO
import sqlite3

import pytest
from werkzeug.datastructures import FileStorage

import services.common as common
import services.sgf_service as sgf_service


SGF_TEXT = "(;GM[1]FF[4]SZ[19]PB[Black]PW[White]RE[W+R];B[pd];W[dd])"


def test_sgf_metadata_preserves_detailed_equivalent_result_and_uses_location():
    rewritten = sgf_service.rewrite_sgf_root(
        "(;EV[Old event]PC[Original place]RE[W+5.5]PB[Old Black];B[pd])",
        {
            "PB": "New Black",
            "PC": "Tournament hall",
            "EV": "New event",
            "DT": "2026-09-11",
            "RE": "W+R",
        },
    )

    assert "PB[New Black]" in rewritten
    assert "PC[Tournament hall]" in rewritten
    assert "EV[New event]" in rewritten
    assert "DT[2026-09-11]" in rewritten
    assert "RE[W+5.5]" in rewritten
    assert rewritten.count("EV[") == 1
    assert rewritten.count("RE[") == 1


def test_sgf_upload_validates_content_and_uses_generated_storage_name(tmp_path, monkeypatch):
    upload_dir = tmp_path / "sgf"
    monkeypatch.setattr(sgf_service, "SGF_UPLOAD_DIR", upload_dir)

    stored_name = sgf_service.save_sgf_upload(
        FileStorage(stream=BytesIO(SGF_TEXT.encode("utf-8")), filename="example.sgf")
    )

    assert stored_name.endswith(".sgf")
    assert stored_name != "example.sgf"
    assert (upload_dir / stored_name).read_text(encoding="utf-8") == SGF_TEXT

    with pytest.raises(ValueError, match=r"Only \.sgf"):
        sgf_service.save_sgf_upload(
            FileStorage(stream=BytesIO(SGF_TEXT.encode("utf-8")), filename="example.txt")
        )
    with pytest.raises(ValueError, match="Invalid SGF"):
        sgf_service.save_sgf_upload(
            FileStorage(stream=BytesIO(b"not an sgf"), filename="example.sgf")
        )
    with pytest.raises(ValueError, match="too large"):
        sgf_service.save_sgf_upload(
            FileStorage(
                stream=BytesIO(("(;GM[1]" + ("A" * sgf_service.MAX_SGF_BYTES)).encode("utf-8")),
                filename="large.sgf",
            )
        )


def test_sgf_schema_migration_adds_optional_match_column():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL
        )
        """
    )

    sgf_service.ensure_sgf_schema(conn)

    assert "sgf_filename" in {
        row[1] for row in conn.execute("PRAGMA table_info(matches)").fetchall()
    }
    conn.close()


def test_admin_upload_and_public_besogo_viewer(admin_client, tmp_path, monkeypatch):
    upload_dir = tmp_path / "sgf"
    monkeypatch.setattr(sgf_service, "SGF_UPLOAD_DIR", upload_dir)

    response = admin_client.post(
        "/admin/matches/add?lang=en",
        data={
            "match_date": "2026-09-11",
            "white_player_id": "1",
            "black_player_id": "2",
            "result": "1-0",
            "event": "SGF test",
            "notes": "Round 1",
            "handicap_stones": "0",
            "sgf_file": (BytesIO(SGF_TEXT.encode("utf-8")), "example.sgf"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    with common.get_db() as conn:
        match = conn.execute(
            "SELECT id, sgf_filename FROM matches WHERE event = 'SGF test'"
        ).fetchone()
    assert match["sgf_filename"].endswith(".sgf")
    assert (upload_dir / match["sgf_filename"]).is_file()

    viewer = admin_client.get(f"/matches/{match['id']}/record?lang=en&theme=dark")
    assert viewer.status_code == 200
    body = viewer.get_data(as_text=True)
    assert "besogo-viewer" in body
    assert "board-dark.css" in body
    assert 'data-besogo-theme="dark"' in body
    assert "besogo.js" in body
    assert "Download SGF" in body
    assert 'sgf="http://' in body

    sgf_response = admin_client.get(f"/matches/{match['id']}/sgf")
    assert sgf_response.status_code == 200
    assert sgf_response.mimetype == "application/x-go-sgf"
    sgf_body = sgf_response.get_data(as_text=True)
    assert "PW[Juan Samper]" in sgf_body
    assert "PB[Camilo Acuna]" in sgf_body
    assert "WR[5k]" in sgf_body
    assert "BR[5k]" in sgf_body
    assert "PC[SGF test]" in sgf_body
    assert "DT[2026-09-11]" in sgf_body
    assert "RE[W+R]" in sgf_body
    assert ";B[pd];W[dd])" in sgf_body


def test_sgf_library_is_public_and_admin_can_link_or_unlink(client, admin_client, tmp_path, monkeypatch):
    upload_dir = tmp_path / "sgf"
    monkeypatch.setattr(sgf_service, "SGF_UPLOAD_DIR", upload_dir)
    stored_name = sgf_service.save_sgf_upload(
        FileStorage(stream=BytesIO(SGF_TEXT.encode("utf-8")), filename="library.sgf")
    )

    library = client.get("/sgf-library?lang=en")
    assert library.status_code == 200
    library_body = library.get_data(as_text=True)
    assert stored_name in library_body
    assert "Black" in library_body
    assert client.get(f"/sgf/{stored_name}").status_code == 200
    assert client.get(f"/sgf-library/{stored_name}?lang=en").status_code == 200

    response = admin_client.post(
        "/admin/sgf/link?lang=en",
        data={"filename": stored_name, "match_id": "1"},
    )
    assert response.status_code == 302
    with common.get_db() as conn:
        assert conn.execute(
            "SELECT sgf_filename FROM matches WHERE id = 1"
        ).fetchone()[0] == stored_name
    linked_body = (upload_dir / stored_name).read_text(encoding="utf-8")
    assert "PW[Juan Samper]" in linked_body
    assert "PB[Camilo Acuna]" in linked_body
    assert "RE[W+R]" in linked_body

    response = admin_client.post(
        "/admin/sgf/unlink?lang=en",
        data={"filename": stored_name, "match_id": "1"},
    )
    assert response.status_code == 302
    with common.get_db() as conn:
        assert conn.execute(
            "SELECT sgf_filename FROM matches WHERE id = 1"
        ).fetchone()[0] is None
    assert (upload_dir / stored_name).is_file()


def test_sgf_links_are_one_to_one_in_both_directions(admin_client, tmp_path, monkeypatch):
    upload_dir = tmp_path / "sgf"
    monkeypatch.setattr(sgf_service, "SGF_UPLOAD_DIR", upload_dir)
    first_name = sgf_service.save_sgf_upload(
        FileStorage(stream=BytesIO(SGF_TEXT.encode("utf-8")), filename="first.sgf")
    )
    second_name = sgf_service.save_sgf_upload(
        FileStorage(stream=BytesIO(SGF_TEXT.encode("utf-8")), filename="second.sgf")
    )

    assert admin_client.post(
        "/admin/sgf/link?lang=en",
        data={"filename": first_name, "match_id": "1"},
    ).status_code == 302
    assert admin_client.post(
        "/admin/sgf/link?lang=en",
        data={"filename": first_name, "match_id": "2"},
    ).status_code == 302
    assert admin_client.post(
        "/admin/sgf/link?lang=en",
        data={"filename": second_name, "match_id": "1"},
    ).status_code == 302

    with common.get_db() as conn:
        links = conn.execute(
            "SELECT id, sgf_filename FROM matches WHERE id IN (1, 2) ORDER BY id"
        ).fetchall()
        assert links[0]["sgf_filename"] == first_name
        assert links[1]["sgf_filename"] is None
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE matches SET sgf_filename = ? WHERE id = 2", (first_name,))
        conn.rollback()


def test_sgf_schema_cleans_legacy_duplicate_links_before_indexing():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_date TEXT NOT NULL,
            white_player_id INTEGER NOT NULL,
            black_player_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            sgf_filename TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO matches
            (id, match_date, white_player_id, black_player_id, result, sgf_filename)
        VALUES (?, '2026-09-13', 1, 2, '1-0', ?)
        """,
        [(1, "duplicate.sgf"), (2, "duplicate.sgf")],
    )

    sgf_service.ensure_sgf_schema(conn)

    links = conn.execute(
        "SELECT id, sgf_filename FROM matches ORDER BY id"
    ).fetchall()
    assert links == [(1, "duplicate.sgf"), (2, None)]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE matches SET sgf_filename = 'duplicate.sgf' WHERE id = 2")
    conn.close()


def test_linking_rejects_malformed_sgf_without_creating_match_link(
    admin_client, tmp_path, monkeypatch
):
    upload_dir = tmp_path / "sgf"
    monkeypatch.setattr(sgf_service, "SGF_UPLOAD_DIR", upload_dir)
    stored_name = sgf_service.save_sgf_upload(
        FileStorage(stream=BytesIO(SGF_TEXT.encode("utf-8")), filename="malformed.sgf")
    )
    path = upload_dir / stored_name
    path.write_text("(;GM[1]", encoding="utf-8")

    response = admin_client.post(
        "/admin/sgf/link?lang=en",
        data={"filename": stored_name, "match_id": "1"},
    )
    assert response.status_code == 302
    with common.get_db() as conn:
        assert conn.execute(
            "SELECT sgf_filename FROM matches WHERE id = 1"
        ).fetchone()[0] is None
    assert path.read_text(encoding="utf-8") == "(;GM[1]"


@pytest.mark.parametrize(
    ("role", "expected_status"),
    [
        ("administrator", 302),
        ("tournament_director", 302),
        ("operator", 302),
        ("member", 403),
    ],
)
def test_sgf_link_permissions_follow_staff_roles(
    client, role, expected_status, tmp_path, monkeypatch
):
    upload_dir = tmp_path / "sgf"
    monkeypatch.setattr(sgf_service, "SGF_UPLOAD_DIR", upload_dir)
    stored_name = sgf_service.save_sgf_upload(
        FileStorage(stream=BytesIO(SGF_TEXT.encode("utf-8")), filename="permissions.sgf")
    )
    user_id = common.create_user_account(
        f"sgf-{role}", "sgf-test-password", role_name=role
    )
    with client.session_transaction() as session:
        session.clear()
        session["user_id"] = user_id

    response = client.post(
        "/admin/sgf/link?lang=en",
        data={"filename": stored_name, "match_id": "1"},
    )
    assert response.status_code == expected_status
    if expected_status == 302:
        with common.get_db() as conn:
            assert conn.execute(
                "SELECT sgf_filename FROM matches WHERE id = 1"
            ).fetchone()[0] == stored_name
            conn.execute("UPDATE matches SET sgf_filename = NULL WHERE id = 1")
            conn.commit()


def test_only_admin_can_delete_sgf_and_delete_clears_match_link(
    client, admin_client, tmp_path, monkeypatch
):
    upload_dir = tmp_path / "sgf"
    monkeypatch.setattr(sgf_service, "SGF_UPLOAD_DIR", upload_dir)
    stored_name = sgf_service.save_sgf_upload(
        FileStorage(stream=BytesIO(SGF_TEXT.encode("utf-8")), filename="delete.sgf")
    )
    with common.get_db() as conn:
        conn.execute("UPDATE matches SET sgf_filename = ? WHERE id = 1", (stored_name,))
        conn.commit()
    with admin_client.session_transaction() as session:
        administrator_id = session["user_id"]

    operator_id = common.create_user_account(
        "sgf-delete-operator", "sgf-test-password", role_name="operator"
    )
    with client.session_transaction() as session:
        session.clear()
        session["user_id"] = operator_id

    response = client.post(
        "/admin/sgf/delete?lang=en",
        data={"filename": stored_name},
    )
    assert response.status_code == 403
    assert (upload_dir / stored_name).is_file()

    with client.session_transaction() as session:
        session.clear()
        session["user_id"] = administrator_id
    response = client.post(
        "/admin/sgf/delete?lang=en",
        data={"filename": stored_name},
    )
    assert response.status_code == 302
    assert not (upload_dir / stored_name).exists()
    with common.get_db() as conn:
        assert conn.execute(
            "SELECT sgf_filename FROM matches WHERE id = 1"
        ).fetchone()[0] is None


def test_missing_sgf_link_self_heals_and_match_delete_keeps_file(admin_client, tmp_path, monkeypatch):
    upload_dir = tmp_path / "sgf"
    monkeypatch.setattr(sgf_service, "SGF_UPLOAD_DIR", upload_dir)
    response = admin_client.post(
        "/admin/matches/add?lang=en",
        data={
            "match_date": "2026-09-12",
            "white_player_id": "1",
            "black_player_id": "2",
            "result": "1-0",
            "event": "Missing SGF test",
            "notes": "Round 1",
            "handicap_stones": "0",
            "sgf_file": (BytesIO(SGF_TEXT.encode("utf-8")), "missing.sgf"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    with common.get_db() as conn:
        match = conn.execute(
            "SELECT id, sgf_filename FROM matches WHERE event = 'Missing SGF test'"
        ).fetchone()
    missing_path = upload_dir / match["sgf_filename"]
    missing_path.unlink()

    assert admin_client.get(f"/matches/{match['id']}/record?lang=en").status_code == 404
    with common.get_db() as conn:
        assert conn.execute(
            "SELECT sgf_filename FROM matches WHERE id = ?", (match["id"],)
        ).fetchone()[0] is None

    response = admin_client.post(
        "/admin/matches/add?lang=en",
        data={
            "match_date": "2026-09-13",
            "white_player_id": "1",
            "black_player_id": "2",
            "result": "0-1",
            "event": "Retained SGF test",
            "notes": "Round 2",
            "handicap_stones": "0",
            "sgf_file": (BytesIO(SGF_TEXT.encode("utf-8")), "retained.sgf"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    with common.get_db() as conn:
        retained_match = conn.execute(
            "SELECT id, sgf_filename FROM matches WHERE event = 'Retained SGF test'"
        ).fetchone()
    retained_path = upload_dir / retained_match["sgf_filename"]

    response = admin_client.post(
        f"/admin/matches/delete?id={retained_match['id']}&lang=en"
    )
    assert response.status_code == 302
    assert retained_path.is_file()
    assert retained_match["sgf_filename"] in admin_client.get("/sgf-library?lang=en").get_data(as_text=True)


def test_sgf_backup_sidecar_restores_library_file(tmp_path, monkeypatch):
    upload_dir = tmp_path / "sgf"
    monkeypatch.setattr(sgf_service, "SGF_UPLOAD_DIR", upload_dir)
    stored_name = sgf_service.save_sgf_upload(
        FileStorage(stream=BytesIO(SGF_TEXT.encode("utf-8")), filename="backup.sgf")
    )
    backup_path = tmp_path / "backup.db"

    sidecar = sgf_service.backup_sgf_files(backup_path)
    (upload_dir / stored_name).unlink()
    assert (sidecar / stored_name).is_file()
    assert sgf_service.restore_sgf_files(backup_path) is True
    assert (upload_dir / stored_name).is_file()
