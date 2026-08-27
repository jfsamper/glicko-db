"""Legacy OpenGotha XML exporter.

Deprecated in favor of the service-layer import helpers in
services.import_gotha and services.import_service.
"""

import csv
import datetime
import logging
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)


def list_xml_files(directory=None):
    """Lists XML files in the current directory with numbered options for user selection."""
    base_dir = Path(directory) if directory is not None else Path.cwd()
    xml_files = sorted(
        path.name for path in base_dir.iterdir() if path.is_file() and path.suffix.lower() == ".xml"
    )

    if not xml_files:
        warnings.warn("No XML files found in the current directory.", UserWarning, stacklevel=2)
        return None

    print("Archivos XML:")
    for i, filename in enumerate(xml_files):
        print(f"{i + 1}. {filename}")

    while True:
        try:
            choice = int(input("Seleccione un archivo: "))
            if 1 <= choice <= len(xml_files):
                return str(base_dir / xml_files[choice - 1])
            print("Invalido. Seleccione un número entre 1 y", len(xml_files))
        except ValueError:
            print("Invalido.")


def _parse_game_rows(xml_path):
    tree = ET.parse(xml_path)
    root = tree.getroot()

    general = root.find("TournamentParameterSet/GeneralParameterSet") or root.find("GeneralParameterSet")
    if general is None:
        raise ValueError("OpenGotha tournament metadata is missing")

    players = []
    for player in root.findall("Players/Player") or root.findall("./Players/Player"):
        first_name = (player.get("firstName") or "").strip()
        last_name = (player.get("name") or "").strip()
        fullname = (last_name + first_name).upper().replace(" ", "")
        players.append([first_name, last_name, fullname])

    date_str = general.get("beginDate")
    if not date_str:
        raise ValueError("OpenGotha tournament beginDate is missing")
    try:
        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid OpenGotha beginDate: {date_str!r}") from exc
    date = date_obj.strftime("%d/%m/%Y")

    event = general.get("name", "")

    games_node = root.find("Games") or root.find("./Games")
    if games_node is None:
        warnings.warn("No <Games> element found in the OpenGotha XML; no matches were exported.", UserWarning, stacklevel=2)
        return []

    games = []
    for game in games_node.findall("Game") or games_node.findall("./Game"):
        round_label = game.get("roundNumber")
        black_name = game.get("blackPlayer")
        white_name = game.get("whitePlayer")
        for player in players:
            if player[2] == black_name:
                black_name = f"{player[1]}, {player[0]}"
            if player[2] == white_name:
                white_name = f"{player[1]}, {player[0]}"
        winner = black_name if game.get("result") == "RESULT_BLACKWINS" else white_name
        games.append([date, f"{round_label}:00 pm", black_name, white_name, winner, event])

    if not games:
        warnings.warn("No <Games> entries were found in the OpenGotha XML; nothing to export.", UserWarning, stacklevel=2)

    return games


def main(xml_path=None):
    warnings.warn(
        "gotha2glicko.py is deprecated; use services.import_gotha.parse_gotha_xml instead.",
        DeprecationWarning,
        stacklevel=2,
    )

    if xml_path is None:
        xml_path = list_xml_files()
        if xml_path is None:
            return []

    games = _parse_game_rows(xml_path)
    if not games:
        logger.warning("No games exported from %s.", xml_path)
        return []

    output_path = Path(xml_path).with_suffix(".csv")
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Fecha", "Ronda", "Negro", "Blanco", "Ganador", "Comentarios"])
        writer.writerows(games)
    logger.info("Exported %s games to %s.", len(games), output_path)
    return games


if __name__ == "__main__":
    main()