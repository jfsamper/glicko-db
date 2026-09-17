from pathlib import Path
import re

from routes.admin import ADMIN_MENU_SECTIONS
from services.i18n import TRANSLATIONS, get_language


def test_unknown_language_falls_back_to_spanish():
    assert get_language("en") == "en"
    assert get_language("fr") == "es"
    assert get_language(None) == "es"


def test_language_priority_order_is_spanish_english_portuguese():
    assert list(TRANSLATIONS.keys()) == ["es", "en", "pt"]


def test_portuguese_is_supported():
    assert get_language("pt") == "pt"
    assert TRANSLATIONS["pt"]["language"] == "Idioma"
    assert TRANSLATIONS["pt"]["report_results"] == "Reportar Resultados"


def test_sorting_labels_are_translated():
    assert TRANSLATIONS["es"]["sort_label"] == "Ordenar por"
    assert TRANSLATIONS["es"]["order_label"] == "Orden"
    assert TRANSLATIONS["es"]["desc_label"] == "Descendente"
    assert TRANSLATIONS["es"]["asc_label"] == "Ascendente"

    assert TRANSLATIONS["en"]["sort_label"] == "Sort by"
    assert TRANSLATIONS["en"]["order_label"] == "Order"
    assert TRANSLATIONS["en"]["desc_label"] == "Descending"
    assert TRANSLATIONS["en"]["asc_label"] == "Ascending"

    assert TRANSLATIONS["pt"]["sort_label"] == "Ordenar por"
    assert TRANSLATIONS["pt"]["order_label"] == "Ordem"
    assert TRANSLATIONS["pt"]["desc_label"] == "Decrescente"
    assert TRANSLATIONS["pt"]["asc_label"] == "Crescente"


def test_portuguese_translations_are_not_mojibake():
    portuguese = TRANSLATIONS["pt"]

    assert portuguese["notes_label"] == "Observações"
    assert portuguese["page_label"] == "Página"
    assert all("Ã" not in value for value in portuguese.values() if isinstance(value, str))


def test_all_languages_have_the_same_translation_keys():
    expected_keys = set(TRANSLATIONS["en"])
    for language, translations in TRANSLATIONS.items():
        assert set(translations) == expected_keys, language


def test_template_translation_keys_exist_in_every_language():
    template_root = Path(__file__).resolve().parents[1] / "templates"
    patterns = (
        re.compile(r"translations\.(?!get\b)([A-Za-z_]\w*)"),
        re.compile(r"translations\s*\[\s*['\"]([^'\"]+)['\"]\s*\]"),
        re.compile(r"translations\.get\(\s*['\"]([^'\"]+)['\"]"),
    )
    template_keys = set()
    for template_path in template_root.rglob("*.html"):
        template = template_path.read_text(encoding="utf-8")
        for pattern in patterns:
            template_keys.update(
                match.group(1) for match in pattern.finditer(template)
            )

    for heading, items in ADMIN_MENU_SECTIONS:
        template_keys.add(heading)
        for _endpoint, title, description, _permission in items:
            template_keys.update((title, description))

    for language, translations in TRANSLATIONS.items():
        missing = template_keys - set(translations)
        assert not missing, f"{language}: {sorted(missing)}"
