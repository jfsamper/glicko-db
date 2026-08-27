import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import gotha2glicko
from services.helpers import normalize_key
from services.import_gotha import parse_gotha_xml
from services.pairing_service import pair_players
from services.standings_service import calculate_standings
from services.tournament_service import read_gotha_tournament


XML_PATH = Path(__file__).parents[1] / "uploads" / "abierto3-26.xml"
FRENCH_XML_PATH = Path(__file__).parents[1] / "uploads" / "french-example.xml"


def test_gotha2glicko_warns_and_handles_missing_games(tmp_path):
    xml_path = tmp_path / "missing_games.xml"
    xml_path.write_text(
        """
        <TournamentParameterSet>
          <GeneralParameterSet name="Test tournament" beginDate="2026-01-01" />
          <Players />
        </TournamentParameterSet>
        """.strip(),
        encoding="utf-8",
    )

    with pytest.warns(DeprecationWarning, match="deprecated"):
        with pytest.warns(UserWarning, match="No <Games>"):
            assert gotha2glicko.main(str(xml_path)) == []


def test_pairing_changes_with_system_and_rating_seed():
    players = [
        {"id": index, "name": f"P{index}", "rating": rating, "score": 0,
         "initial_score": 0, "acceleration": 0, "opponents": set(),
         "colors": {"white": 0, "black": 0}}
        for index, rating in enumerate((2400, 2200, 2000, 1800), 1)
    ]
    swiss = pair_players(players, "swiss")
    accelerated = pair_players(
        [dict(player, acceleration=1 if player["id"] <= 2 else 0) for player in players],
        "accelerated_swiss",
    )
    mcmahon = pair_players(
        [dict(player, initial_score=1 if player["id"] <= 2 else 0) for player in players],
        "mcmahon",
    )
    assert all(not pairing["is_bye"] for pairing in swiss + accelerated + mcmahon)
    assert {frozenset((p["white_player_id"], p["black_player_id"])) for p in swiss} == {
        frozenset((1, 2)), frozenset((3, 4))
    }
    assert len({
        tuple(sorted((p["white_player_id"], p["black_player_id"])))
        for pairings in (swiss, accelerated, mcmahon)
        for p in pairings
    }) >= 2


def test_opengotha_fixture_standings_and_tiebreaks():
    """Verify Swiss standings tiebreak semantics (Score -> SOS -> SOSOS -> SODOS) on OpenGotha XML."""
    root = ET.parse(XML_PATH).getroot()
    players_map = {}
    for idx, p in enumerate(root.findall("Players/Player"), 1):
        fn = p.get("firstName", "").strip()
        ln = p.get("name", "").strip()
        key = normalize_key(f"{ln}{fn}")
        players_map[key] = {
            "id": idx,
            "name": f"{fn} {ln}".strip(),
            "rating": float(p.get("rating", 1500)),
            "initial_score": 0.0,
            "category": p.get("grade", ""),
        }

    games = []
    for g in root.findall("Games/Game"):
        w_key = normalize_key(g.get("whitePlayer", ""))
        b_key = normalize_key(g.get("blackPlayer", ""))
        res = g.get("result", "")
        if res == "RESULT_WHITEWINS":
            res_str = "1-0"
        elif res == "RESULT_BLACKWINS":
            res_str = "0-1"
        elif res == "RESULT_EQUAL":
            res_str = "1/2-1/2"
        else:
            continue
        games.append({
            "white_player_id": players_map[w_key]["id"],
            "black_player_id": players_map[b_key]["id"],
            "result": res_str,
            "round_number": int(g.get("roundNumber", 1)),
        })

    standings = calculate_standings(list(players_map.values()), games, tournament_type="swiss")

    assert len(standings) == 10
    # Rank 1: Juan David Ramirez (undefeated 5-0)
    assert standings[0]["name"] == "Juan David Ramirez"
    assert standings[0]["score"] == 5.0
    assert standings[0]["sos"] == 12.0
    assert standings[0]["sosos"] == 71.0
    assert standings[0]["sodos"] == 12.0

    # Rank 2: Diego Rodriguez (4-1)
    assert standings[1]["name"] == "Diego Rodriguez"
    assert standings[1]["score"] == 4.0
    assert standings[1]["sos"] == 14.0

    # Ranks 3, 4, 5 are tied with 3.0 points and broken by SOS (16 > 11 > 9)
    assert [s["name"] for s in standings[2:5]] == ["Juan Felipe Burgos", "Brandal Henao", "Santiago Espinosa"]
    assert standings[2]["score"] == standings[3]["score"] == standings[4]["score"] == 3.0
    assert standings[2]["sos"] == 16.0
    assert standings[3]["sos"] == 11.0
    assert standings[4]["sos"] == 9.0

    # Ranks 6, 7 have 2.0 points, broken by SOS (17 > 12)
    assert standings[5]["name"] == "Juan Felipe Samper"
    assert standings[5]["score"] == 2.0
    assert standings[5]["sos"] == 17.0
    assert standings[6]["name"] == "Jorge Gonzalez"
    assert standings[6]["score"] == 2.0
    assert standings[6]["sos"] == 12.0

    # Ranks 8, 9, 10 have 1.0 point, broken by SOS / SOSOS / rating
    assert [s["score"] for s in standings[7:10]] == [1.0, 1.0, 1.0]
    assert standings[7]["sos"] == 12.0
    assert standings[8]["sos"] == 12.0
    assert standings[7]["sosos"] > standings[8]["sosos"]  # 62.0 > 56.0


def test_opengotha_mcmahon_fixture_standings_and_tiebreaks():
    """Verify McMahon tournament standings and tiebreaks against OpenGotha McMahon fixture."""
    root = ET.parse(FRENCH_XML_PATH).getroot()
    players_map = {}
    for idx, p in enumerate(root.findall("Players/Player"), 1):
        fn = p.get("firstName", "").strip()
        ln = p.get("name", "").strip()
        key = normalize_key(f"{ln}{fn}")
        players_map[key] = {
            "id": idx,
            "name": f"{fn} {ln}".strip(),
            "rating": float(p.get("rating", 1500)),
            "initial_score": float(p.get("smmsCorrection", 0)),
            "category": p.get("grade", ""),
        }

    games = []
    for g in root.findall("Games/Game"):
        w_key = normalize_key(g.get("whitePlayer", ""))
        b_key = normalize_key(g.get("blackPlayer", ""))
        res = g.get("result", "")
        res_str = "1-0" if res == "RESULT_WHITEWINS" else "0-1" if res == "RESULT_BLACKWINS" else "1/2-1/2" if res == "RESULT_EQUAL" else None
        if res_str and w_key in players_map and b_key in players_map:
            games.append({
                "white_player_id": players_map[w_key]["id"],
                "black_player_id": players_map[b_key]["id"],
                "result": res_str,
                "round_number": int(g.get("roundNumber", 1)),
            })

    standings = calculate_standings(list(players_map.values()), games, tournament_type="mcmahon")

    assert len(standings) == 19
    # Winner: Davide Minieri (4.0 points)
    assert standings[0]["name"] == "Davide Minieri"
    assert standings[0]["score"] == 4.0
    assert standings[0]["primary_score"] == 4.0
    assert standings[0]["sos"] == 5.0
    assert standings[0]["sosos"] == 32.0

    # Ranks 2-6 are tied with 3.0 points and broken deterministically by SOS / SOSOS / SODOS
    assert [s["score"] for s in standings[1:6]] == [3.0, 3.0, 3.0, 3.0, 3.0]
    assert standings[1]["sos"] >= standings[2]["sos"] >= standings[3]["sos"]
