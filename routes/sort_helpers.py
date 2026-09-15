"""Shared sort fields and query-parameter parsers for route lists."""

MATCH_SORT_FIELDS = {
    "date": "m.match_date",
    "white": "LOWER(p_white.display_name)",
    "black": "LOWER(p_black.display_name)",
    "result": "CASE m.result WHEN '1-0' THEN 0 WHEN '1/2-1/2' THEN 1 WHEN '0-1' THEN 2 ELSE 3 END",
    "round": "m.round_number",
}


def _parse_match_sort(sort_value, default_sort="date"):
    key = (sort_value or default_sort).strip().lower()
    return key if key in MATCH_SORT_FIELDS else default_sort


def _parse_match_order(order_value):
    value = (order_value or "desc").strip().lower()
    return value if value in {"asc", "desc"} else "desc"


TOURNAMENT_SORT_FIELDS = {
    "name": "LOWER(t.name)",
    "date": "COALESCE(t.begin_date, t.created_at)",
    "status": "CASE t.status WHEN 'draft' THEN 0 WHEN 'active' THEN 1 WHEN 'canceled' THEN 2 WHEN 'completed' THEN 3 ELSE 4 END",
    "participants": "0",
}


def parse_tournament_sort(sort_value, default_sort="date"):
    key = (sort_value or default_sort).strip().lower()
    return key if key in TOURNAMENT_SORT_FIELDS else default_sort


def parse_tournament_order(order_value, default_order="desc"):
    value = (order_value or default_order).strip().lower()
    return value if value in {"asc", "desc"} else default_order
