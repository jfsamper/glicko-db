"""Optional browser regressions: install Playwright and its Chromium browser to run."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def navigation_browser():
    with playwright_api.sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip("Playwright Chromium is not installed")
        with playwright.chromium.launch() as browser:
            yield browser


@pytest.fixture
def navigation_page(navigation_browser, admin_client, request):
    options = getattr(request, "param", {"viewport": {"width": 1280, "height": 900}})
    with navigation_browser.new_context(**options) as context:
        page = context.new_page()

        def serve_app(route):
            url = urlsplit(route.request.url)
            if url.hostname != "navigation.test":
                route.abort()
                return
            path = url.path + (f"?{url.query}" if url.query else "")
            response = admin_client.get(path)
            route.fulfill(
                status=response.status_code,
                content_type=response.content_type,
                body=response.get_data(),
            )

        page.route("**/*", serve_app)
        yield page


@pytest.mark.parametrize("path", ["/", "/admin", "/admin/profile", "/admin/report-results"])
def test_user_menu_starts_closed_on_each_page(navigation_page, path):
    page = navigation_page
    page.goto(f"http://navigation.test{path}?lang=en")
    playwright_api.expect(page.locator(".sidebar")).to_have_count(0)
    playwright_api.expect(page.locator(".footer-contact")).to_be_visible()
    playwright_api.expect(page.locator(".top-nav-submenu")).not_to_have_attribute("open")
    playwright_api.expect(page.locator(".top-nav-submenu-menu")).not_to_be_visible()


@pytest.mark.parametrize("width", [1280, 800])
def test_user_menu_hover_and_pointer_travel(navigation_page, width):
    page = navigation_page
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("http://navigation.test/?lang=en")
    if width <= 900:
        page.locator(".top-nav-toggle").click()
    summary = page.locator(".top-nav-submenu > summary")
    menu = page.locator(".top-nav-submenu-menu")
    summary.hover()
    playwright_api.expect(menu).to_be_visible()
    box = menu.bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 20, steps=20)
    playwright_api.expect(menu).to_be_visible()
    page.mouse.move(0, 0)
    playwright_api.expect(menu).not_to_be_visible()


def test_user_menu_click_does_not_close_hamburger(navigation_page):
    page = navigation_page
    page.set_viewport_size({"width": 800, "height": 900})
    page.goto("http://navigation.test/?lang=en")
    toggle = page.locator(".top-nav-toggle")
    toggle.click()
    page.locator(".top-nav-submenu > summary").click()
    playwright_api.expect(toggle).to_have_attribute("aria-expanded", "true")
    playwright_api.expect(page.locator(".top-nav-submenu-menu")).to_be_visible()
    toggle.click()
    toggle.click()
    playwright_api.expect(page.locator(".top-nav-submenu-menu")).not_to_be_visible()


@pytest.mark.parametrize("width", [390, 1280])
def test_user_menu_trigger_shows_stateful_submenu_indicator(navigation_page, width):
    page = navigation_page
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("http://navigation.test/?lang=en")
    if width <= 900:
        page.locator(".top-nav-toggle").click()
    trigger = page.locator(".top-nav-submenu-trigger")
    closed_transform = trigger.evaluate(
        "(element) => getComputedStyle(element, '::after').transform"
    )
    assert closed_transform != "none"
    trigger.click()
    playwright_api.expect(page.locator(".top-nav-submenu")).to_have_attribute("open", "")
    page.wait_for_timeout(200)
    open_transform = trigger.evaluate(
        "(element) => getComputedStyle(element, '::after').transform"
    )
    assert open_transform != closed_transform


@pytest.mark.parametrize(
    ("navigation_page", "language"),
    [
        ({"has_touch": True, "is_mobile": True, "viewport": {"width": 320, "height": 844}}, "en"),
        ({"has_touch": True, "is_mobile": True, "viewport": {"width": 390, "height": 844}}, "es"),
        ({"has_touch": True, "is_mobile": True, "viewport": {"width": 800, "height": 900}}, "pt"),
    ],
    indirect=["navigation_page"],
)
def test_user_menu_touch_flyout_and_reset(navigation_page, language):
    page = navigation_page
    page.goto(f"http://navigation.test/?lang={language}")
    toggle = page.locator(".top-nav-toggle")
    summary = page.locator(".top-nav-submenu > summary")
    menu = page.locator(".top-nav-submenu-menu")
    toggle.tap()
    summary.tap()
    playwright_api.expect(toggle).to_have_attribute("aria-expanded", "true")
    playwright_api.expect(menu).to_be_visible()
    menu_box = menu.bounding_box()
    summary_box = summary.bounding_box()
    assert menu_box is not None and summary_box is not None
    assert menu_box["x"] >= 0
    assert menu_box["x"] + menu_box["width"] <= summary_box["x"] + 1
    page.wait_for_timeout(5500)
    playwright_api.expect(menu).to_be_visible()
    summary.tap()
    playwright_api.expect(menu).not_to_be_visible()
    summary.tap()
    toggle.tap()
    toggle.tap()
    playwright_api.expect(menu).not_to_be_visible()


@pytest.mark.parametrize("width", [1280, 800])
def test_user_menu_keyboard_and_outside_dismissal(navigation_page, width):
    page = navigation_page
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("http://navigation.test/?lang=en")
    if width <= 900:
        page.locator(".top-nav-toggle").click()
    summary = page.locator(".top-nav-submenu > summary")
    menu = page.locator(".top-nav-submenu-menu")
    playwright_api.expect(summary).to_be_visible()
    summary.focus()
    summary.press("Enter")
    playwright_api.expect(menu).to_be_visible()
    summary.press("Tab")
    playwright_api.expect(menu.locator("a").first).to_be_focused()
    page.keyboard.press("Escape")
    playwright_api.expect(menu).not_to_be_visible()
    if width <= 900:
        page.locator(".top-nav-toggle").click()
    playwright_api.expect(summary).to_be_visible()
    summary.focus()
    summary.press("Enter")
    page.locator("footer p").click()
    playwright_api.expect(menu).not_to_be_visible()


def test_user_menu_closes_on_link_navigation_and_back(navigation_page):
    page = navigation_page
    page.goto("http://navigation.test/?lang=en")
    page.locator(".top-nav-submenu > summary").hover()
    page.locator(".top-nav-submenu-menu a[href*='/admin/profile']").click()
    playwright_api.expect(page).to_have_url("http://navigation.test/admin/profile?lang=en")
    playwright_api.expect(page.locator(".top-nav-submenu-menu")).not_to_be_visible()
    page.go_back()
    playwright_api.expect(page.locator(".top-nav-submenu-menu")).not_to_be_visible()


@pytest.mark.parametrize("width", [320, 390, 800, 1280])
def test_contact_footer_and_user_greeting_fit_layout(navigation_page, width):
    page = navigation_page
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("http://navigation.test/?lang=en")
    if width <= 900:
        page.locator(".top-nav-toggle").click()
    page.locator(".top-nav-submenu > summary").click()
    playwright_api.expect(page.locator(".top-nav-submenu-greeting")).to_have_text("Welcome, admin")
    playwright_api.expect(page.locator(".footer-contact a[href^='mailto:']")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
