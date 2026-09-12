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
