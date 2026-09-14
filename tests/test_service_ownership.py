from services import chart_service, common, i18n


def test_translations_and_chart_helpers_have_dedicated_owners():
    assert common.TRANSLATIONS is i18n.TRANSLATIONS
    assert common.build_rating_chart_data is chart_service.build_rating_chart_data
    assert common.build_smooth_path is chart_service.build_smooth_path