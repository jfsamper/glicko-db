"""Country codes, localized display names, and regional groupings."""
from functools import lru_cache
import logging
import re
import unicodedata

from babel import Locale
import pycountry

logger = logging.getLogger(__name__)

LATIN_AMERICA_AND_CARIBBEAN_COUNTRIES = frozenset({
    "ABW", "AIA", "ARG", "ATG", "BHS", "BLM", "BLZ", "BOL", "BRA",
    "BRB", "BES", "CHL", "COL", "CRI", "CUB", "CUW", "CYM", "DMA",
    "DOM", "ECU", "FLK", "GLP", "GRD", "GUF", "GTM", "GUY", "HTI",
    "HND", "JAM", "KNA", "LCA", "MAF", "MEX", "MSR", "MTQ", "NIC",
    "PAN", "PER", "PRI", "PRY", "SXM", "SUR", "TCA", "TTO", "URY",
    "VCT", "VEN", "VGB", "VIR",
})
REGIONS = {
    "latin-america-caribbean": LATIN_AMERICA_AND_CARIBBEAN_COUNTRIES,
}
SUPPORTED_GROUPS = frozenset({"u18", "women"})


def _name_key(value):
    decomposed = unicodedata.normalize("NFKD", str(value))
    ascii_value = decomposed.encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", " ", ascii_value).strip()


def _build_country_indexes():
    countries = {}
    alpha2_to_alpha3 = {}
    aliases = {}

    for country in pycountry.countries:
        countries[country.alpha_3] = country
        alpha2_to_alpha3[country.alpha_2] = country.alpha_3
        names = {
            country.name,
            getattr(country, "common_name", ""),
            getattr(country, "official_name", ""),
        }
        for language in ("en", "es", "pt"):
            localized_name = Locale.parse(language).territories.get(country.alpha_2)
            if localized_name:
                names.add(localized_name)
        for name in names:
            if name:
                aliases.setdefault(_name_key(name), set()).add(country.alpha_3)

    unique_aliases = {
        key: next(iter(codes))
        for key, codes in aliases.items()
        if len(codes) == 1 and key
    }
    return countries, alpha2_to_alpha3, unique_aliases


_COUNTRIES, _ALPHA2_TO_ALPHA3, _COUNTRY_ALIASES = _build_country_indexes()


def normalize_country_code(value):
    """Return a canonical ISO 3166-1 alpha-3 code or raise for unknown input."""
    if value is None or not str(value).strip():
        return None

    text = str(value).strip()
    code = text.upper()
    if code in _COUNTRIES:
        return code
    if code in _ALPHA2_TO_ALPHA3:
        return _ALPHA2_TO_ALPHA3[code]

    country_code = _COUNTRY_ALIASES.get(_name_key(text))
    if country_code:
        return country_code
    raise ValueError("Unknown country code or name")


def country_name(value, language="en"):
    """Return a localized country name for an alpha-2, alpha-3, or known name."""
    if value is None or not str(value).strip():
        return ""
    try:
        code = normalize_country_code(value)
    except ValueError:
        return str(value).strip()
    country = _COUNTRIES[code]
    locale = Locale.parse(language if language in {"en", "es", "pt"} else "en")
    return locale.territories.get(country.alpha_2, country.name)


@lru_cache(maxsize=3)
def country_options(language="en"):
    """Return localized country labels paired with canonical alpha-3 values."""
    options = [
        {
            "code": country.alpha_3,
            "name": country_name(country.alpha_3, language),
        }
        for country in _COUNTRIES.values()
    ]
    return tuple(sorted(options, key=lambda option: option["name"].casefold()))


def country_name_map(language="en"):
    return {
        option["code"]: option["name"]
        for option in country_options(language)
    }


def migrate_player_country_codes(conn):
    """Normalize recognized legacy country values without discarding unknown values."""
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(players)").fetchall()
    }
    if not {"id", "country"}.issubset(columns):
        return

    unmapped_count = 0
    rows = conn.execute(
        "SELECT id, country FROM players WHERE country IS NOT NULL AND TRIM(country) <> ''"
    ).fetchall()
    for row in rows:
        try:
            normalized = normalize_country_code(row[1])
        except ValueError:
            unmapped_count += 1
            continue
        if normalized != row[1]:
            conn.execute(
                "UPDATE players SET country = ? WHERE id = ?",
                (normalized, row[0]),
            )
    if unmapped_count:
        logger.warning(
            "Preserved %d player country values that could not be normalized",
            unmapped_count,
        )
