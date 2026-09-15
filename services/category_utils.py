"""Pure helpers for converting Glicko ratings to category labels."""
import math

from config import DEFAULT_RATING


def format_glicko_category(glicko, decimals=0, k=None, m=None):
    """Convert a rating to a kyu/dan label using the supplied category scale."""
    if not k or not m:
        raise ValueError("Category parameters must be non-zero")

    try:
        glicko = float(glicko)
    except (TypeError, ValueError):
        glicko = DEFAULT_RATING
    if not math.isfinite(glicko) or glicko <= 0:
        glicko = DEFAULT_RATING

    value = (math.log(glicko / m) * k) - 29

    if decimals == 0:
        rounded_value = math.floor(value)

        if rounded_value < 0:
            return f"{abs(rounded_value)} kyu"

        return f"{rounded_value + 1} dan"

    rounded_value = round(value, decimals)

    if rounded_value < 0:
        return f"{1 - rounded_value:.{decimals}f} kyu"

    return f"{rounded_value + 1:.{decimals}f} dan"
