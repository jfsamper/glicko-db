# services/import_gotha.py
"""Service for importing tournament data from OpenGotha XML files."""
from dataclasses import asdict, dataclass
from typing import Any, Mapping
import xml.etree.ElementTree as ET
import datetime


@dataclass
class GothaPlayer:
    """Typed participant data read from an OpenGotha tournament."""

    key: str
    display_name: str
    rating: float
    rank: int
    rank_value: int
    category: str
    country: str
    club: str

    def __getitem__(self, field_name: str) -> Any:
        try:
            return getattr(self, field_name)
        except AttributeError as exc:
            raise KeyError(field_name) from exc

    def get(self, field_name: str, default: Any = None) -> Any:
        return getattr(self, field_name, default)


@dataclass
class GothaMatch:
    """Typed match data read from an OpenGotha tournament."""

    match_date: str
    round: str | None
    white: str | None
    black: str | None
    winner: str | None
    result: str
    event: str

    def __getitem__(self, field_name: str) -> Any:
        try:
            return getattr(self, field_name)
        except AttributeError as exc:
            raise KeyError(field_name) from exc

    def get(self, field_name: str, default: Any = None) -> Any:
        return getattr(self, field_name, default)

    def to_dict(self) -> dict[str, Any]:
        """Return the historical dictionary shape for compatibility boundaries."""
        return asdict(self)


@dataclass
class GothaTournamentPayload:
    """Typed tournament metadata read from an OpenGotha XML document."""

    name: str
    short_name: str
    location: str
    begin_date: str
    end_date: str
    rounds: int
    players: list[GothaPlayer]
    pairing_parameters: dict[str, str]
    tournament_type: str
    pairing_system: str
    bye_points: float
    absent_points: float
    mm_bar: int
    mm_floor: int
    mm_zero: int
    placement_criteria: str

    def __getitem__(self, field_name: str) -> Any:
        try:
            return getattr(self, field_name)
        except AttributeError as exc:
            raise KeyError(field_name) from exc

    def get(self, field_name: str, default: Any = None) -> Any:
        return getattr(self, field_name, default)

    def update(self, values: Mapping[str, Any] | None = None, **kwargs: Any) -> None:
        """Apply legacy metadata overrides while keeping attribute access typed."""
        updates = dict(values or {})
        updates.update(kwargs)
        for field_name, value in updates.items():
            setattr(self, field_name, value)

    def to_dict(self) -> dict[str, Any]:
        """Return the historical dictionary shape for compatibility boundaries."""
        return asdict(self)


def parse_gotha_xml(xml_path) -> list[GothaMatch]:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    tournament_root = root.find("TournamentParameterSet") if root.tag != "TournamentParameterSet" else root
    if tournament_root is None:
        tournament_root = root

    general = tournament_root.find("GeneralParameterSet")
    if general is None:
        general = root.find("GeneralParameterSet")
    if general is None:
        raise ValueError("OpenGotha tournament metadata is missing")

    players = []
    players_container = tournament_root.find("Players")
    if players_container is None:
        players_container = root.find("Players")
    for player in (players_container.findall("Player") if players_container is not None else []):
        first_name = (player.get("firstName") or "").strip()
        last_name = (player.get("name") or "").strip()

        fullname = (last_name + first_name).upper().replace(" ", "")

        players.append(
            {
                "first": first_name,
                "last": last_name,
                "key": fullname,
            }
        )

    date_str = general.get("beginDate")
    if not date_str:
        raise ValueError("OpenGotha tournament beginDate is missing")

    try:
        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid OpenGotha beginDate: {date_str!r}") from exc

    event_date = date_obj.strftime("%Y-%m-%d")
    event_name = general.get("name", "")

    matches: list[GothaMatch] = []

    for game in root.findall("Games/Game"):

        round_number = game.get("roundNumber")

        black_key = game.get("blackPlayer")
        white_key = game.get("whitePlayer")

        black_name = None
        white_name = None

        for player in players:
            if player["key"] == black_key:
                black_name = f"{player['last']}, {player['first']}".strip(", ")

            if player["key"] == white_key:
                white_name = f"{player['last']}, {player['first']}".strip(", ")

        result_code = game.get("result")

        if result_code == "RESULT_BLACKWINS":
            result = "0-1"
            winner = black_name

        elif result_code == "RESULT_WHITEWINS":
            result = "1-0"
            winner = white_name

        else:
            result = "1/2-1/2"
            winner = ""

        matches.append(
            GothaMatch(
                match_date=event_date,
                round=round_number,
                white=white_name,
                black=black_name,
                winner=winner,
                result=result,
                event=event_name,
            )
        )

    return matches