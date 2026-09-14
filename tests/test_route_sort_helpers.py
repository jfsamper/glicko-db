from routes import admin, public


def test_admin_and_public_match_sort_helpers_share_one_definition():
    assert admin.MATCH_SORT_FIELDS is public.MATCH_SORT_FIELDS
    assert admin._parse_match_sort("ROUND") == "round"
    assert public._parse_match_order("invalid") == "desc"


def test_admin_and_public_tournament_sort_helpers_share_one_definition():
    assert admin.TOURNAMENT_SORT_FIELDS is public.TOURNAMENT_SORT_FIELDS
    assert admin.parse_tournament_sort("STATUS") == "status"
    assert public.parse_tournament_order("invalid", default_order="asc") == "asc"