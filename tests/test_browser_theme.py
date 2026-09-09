"""Exercise the homepage and a real bilingual PyPTO build in Chromium.

Set PYPTO_DOCS_SITE to the generated PyPTO site directory, then run
``python -m pytest tests/test_browser_theme.py``. Install pytest, playwright,
and its Chromium browser first. No published site is modified or required.
"""

from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
from threading import Thread
from urllib.parse import urljoin, urlsplit

import pytest
from playwright.sync_api import (
    Browser,
    ConsoleMessage,
    Page,
    Route,
    expect,
    sync_playwright,
)


@pytest.fixture(scope="session")
def site_origin() -> Iterator[str]:
    theme_root = Path(__file__).resolve().parents[1]
    configured_site = os.environ.get("PYPTO_DOCS_SITE")
    if not configured_site:
        pytest.fail("Set PYPTO_DOCS_SITE to a generated bilingual PyPTO site directory")
    docs_site = Path(configured_site).resolve()
    for required in ("index.html", "zh/index.html"):
        assert (docs_site / required).is_file(), (
            f"Missing generated page: {docs_site / required}"
        )

    class SiteHandler(SimpleHTTPRequestHandler):
        def translate_path(self, path: str) -> str:
            if urlsplit(path).path.startswith("/pypto/"):
                self.directory = str(docs_site)
                path = path[len("/pypto") :]
            else:
                self.directory = str(theme_root)
            return super().translate_path(path)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SiteHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser: Browser, site_origin: str) -> Iterator[Page]:
    context = browser.new_context(
        viewport={"width": 1440, "height": 1000},
        color_scheme="light",
        reduced_motion="reduce",
    )

    def local_destination(route: Route) -> None:
        # Keep real production links in the HTML, but follow their paths on our
        # single test origin so the browser exercises shared storage and clicks.
        destination = urlsplit(route.request.url)
        suffix = destination.path + (
            f"?{destination.query}" if destination.query else ""
        )
        route.fulfill(status=302, headers={"Location": site_origin + suffix})

    context.route("https://www.pypto.ai/**", local_destination)
    # Repository counters are unrelated external metadata. Keep these tests
    # independent of GitHub availability and rate limits, without replacing any
    # generated HTML, theme code, or search data.
    context.route(
        "https://api.github.com/repos/**",
        lambda route: route.fulfill(
            json={"stargazers_count": 0, "forks_count": 0, "tag_name": "test"},
            headers={"Access-Control-Allow-Origin": "*"},
        ),
    )
    current = context.new_page()
    errors: list[str] = []

    def optional_sitemap(url: str) -> bool:
        # Reproduced with unmodified PyPTO on Material 9.7.7: Material probes a
        # sitemap beside each i18n alternate page, although static-i18n emits
        # only the root sitemap. Exempt just those optional 404s for real pages;
        # the root sitemap, shared assets, and all other browser errors stay strict.
        if not url.startswith(site_origin + "/pypto/"):
            return False
        path = urlsplit(url).path.removeprefix("/pypto/")
        if path == "sitemap.xml" or not path.endswith("/sitemap.xml"):
            return False
        article = path.removesuffix("sitemap.xml")
        return (Path(os.environ["PYPTO_DOCS_SITE"]) / article / "index.html").is_file()

    def console_error(message: ConsoleMessage) -> None:
        if message.type != "error":
            return
        url = message.location.get("url", "")
        if message.text.startswith(
            "Failed to load resource: the server responded with a status of 404"
        ) and optional_sitemap(url):
            return
        errors.append(f"{message.text} ({url})")

    current.on("pageerror", lambda error: errors.append(str(error)))
    current.on("console", console_error)
    current.on(
        "response",
        lambda response: errors.append(f"HTTP {response.status}: {response.url}")
        if response.status >= 400
        and not (response.status == 404 and optional_sitemap(response.url))
        else None,
    )
    try:
        yield current
    finally:
        context.close()
        assert not errors, "Browser errors:\n" + "\n".join(errors)


def visit(page: Page, origin: str, path: str) -> None:
    response = page.goto(origin + path, wait_until="load")
    assert response is not None and response.ok, f"Could not load {path}"


def assert_no_horizontal_overflow(page: Page) -> None:
    size = page.evaluate("""() => ({
        viewport: window.innerWidth,
        document: document.documentElement.scrollWidth,
        body: document.body.scrollWidth
    })""")
    assert max(size["document"], size["body"]) <= size["viewport"] + 1, size


def assert_theme(page: Page, mode: str, effective: str, *, docs: bool) -> None:
    expect(page.locator("[data-pypto-theme-select]")).to_have_value(mode)
    expect(page.locator("html")).to_have_attribute("data-pypto-theme", effective)
    if docs:
        scheme = "slate" if effective == "dark" else "default"
        expect(page.locator("body")).to_have_attribute("data-md-color-scheme", scheme)


@pytest.mark.parametrize("width", [320, 390, 768, 1440])
@pytest.mark.parametrize("path", ["/pypto/", "/pypto/zh/"])
def test_project_menu_fits_and_closes_with_keyboard(
    page: Page, site_origin: str, width: int, path: str
) -> None:
    page.set_viewport_size({"width": width, "height": 1000})
    visit(page, site_origin, path)
    summary = page.locator(".pypto-project-switcher summary")
    summary.focus()
    summary.press("Enter")
    menu = page.locator(".pypto-project-menu")
    expect(menu).to_be_visible()
    bounds = menu.bounding_box()
    assert bounds is not None
    assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1, bounds
    assert_no_horizontal_overflow(page)
    page.keyboard.press("Escape")
    expect(menu).not_to_be_visible()
    expect(summary).to_be_focused()


@pytest.mark.parametrize("locale", ["en", "zh"])
def test_project_links_preserve_supported_language(
    page: Page, site_origin: str, locale: str
) -> None:
    visit(page, site_origin, "/pypto/zh/" if locale == "zh" else "/pypto/")
    page.locator(".pypto-project-switcher summary").click()
    menu = page.locator(".pypto-project-menu")
    for project in ("pypto", "simpler", "pypto-lib", "pypto-serving", "pypto-tools"):
        translated = locale == "zh" and project in {"pypto", "pypto-tools"}
        target = f"https://www.pypto.ai/{project}/" + ("zh/" if translated else "")
        link = menu.locator(f'a[href="{target}"]')
        expect(link).to_be_visible()
        if locale == "zh" and not translated:
            expect(link.locator("small")).to_have_text("English")
    current = menu.locator("a[aria-current]")
    expect(current).to_have_count(1)
    expected = "https://www.pypto.ai/pypto/" + ("zh/" if locale == "zh" else "")
    expect(current).to_have_attribute("href", expected)
    expect(menu.locator(".pypto-project-home")).to_have_attribute(
        "href", "https://www.pypto.ai/"
    )


@pytest.mark.parametrize("mode", ["system", "light", "dark"])
def test_theme_persists_across_homepage_and_languages(
    page: Page, site_origin: str, mode: str
) -> None:
    page.emulate_media(color_scheme="dark")
    visit(page, site_origin, "/")
    assert_theme(page, "system", "dark", docs=False)
    page.locator("[data-pypto-theme-select]").select_option(mode)
    effective = "light" if mode == "light" else "dark"
    assert_theme(page, mode, effective, docs=False)

    page.get_by_role("link", name="PyPTO documentation", exact=True).click()
    expect(page).to_have_url(site_origin + "/pypto/")
    assert_theme(page, mode, effective, docs=True)
    page.locator(".md-select button").click()
    page.locator('.md-select__link[hreflang="zh"]').click()
    expect(page).to_have_url(site_origin + "/pypto/zh/")
    assert_theme(page, mode, effective, docs=True)

    page.emulate_media(color_scheme="light")
    effective = "dark" if mode == "dark" else "light"
    assert_theme(page, mode, effective, docs=True)
    page.locator(".pypto-brand").click()
    expect(page).to_have_url(site_origin + "/")
    assert_theme(page, mode, effective, docs=False)


def test_storage_denied_keeps_theme_controls_working(
    page: Page, site_origin: str
) -> None:
    page.add_init_script("""(() => {
        for (const method of ["getItem", "setItem"]) {
            Storage.prototype[method] = () => {
                throw new DOMException("Storage disabled for this test", "SecurityError");
            };
        }
    })();""")
    page.emulate_media(color_scheme="dark")
    for path in ("/", "/pypto/", "/pypto/zh/"):
        visit(page, site_origin, path)
        assert_theme(page, "system", "dark", docs=path != "/")
        page.locator("[data-pypto-theme-select]").select_option("light")
        assert_theme(page, "light", "light", docs=path != "/")
        page.locator("[data-pypto-theme-select]").select_option("system")
        assert_theme(page, "system", "dark", docs=path != "/")


def test_documentation_search_returns_navigable_results(
    page: Page, site_origin: str
) -> None:
    visit(page, site_origin, "/pypto/")
    query = page.locator('[data-md-component="search-query"]')
    expect(query).to_have_attribute("aria-label", "Search PyPTO")
    query.fill("matmul")
    # Cross-language results are misrouted even by unmodified Material 9.7.7
    # with static-i18n. Exercise navigation within the current locale here.
    result = page.locator('.md-search-result__link:not([href*="/pypto/zh/"])').first
    expect(result).to_be_visible(timeout=20000)
    target = result.get_attribute("href")
    assert target
    destination = urljoin(page.url, target)
    result.click()
    expect(page).to_have_url(destination)
    page.wait_for_load_state("load")
    assert urlsplit(page.url).path.startswith("/pypto/")
    expect(page.locator(".md-content h1").first).to_be_visible()


@pytest.mark.parametrize("width", [320, 1440])
@pytest.mark.parametrize(
    "path",
    [
        "/pypto/dev/passes/00-pass_manager/",
        "/pypto/api/language/",
        "/pypto/zh/api/language/",
    ],
)
def test_deep_pages_keep_navigation_and_content_in_view(
    page: Page, site_origin: str, width: int, path: str
) -> None:
    page.set_viewport_size({"width": width, "height": 1000})
    visit(page, site_origin, path)
    assert_no_horizontal_overflow(page)
    if width == 320:
        page.locator('.pypto-header label[for="__drawer"]').click()
        expect(page.locator("#__drawer")).to_be_checked()
        expect(
            page.locator(".md-nav--primary .md-nav__link--active:visible").first
        ).to_be_visible()
        page.locator(".md-overlay").click(position={"x": width - 8, "y": 200})
        expect(page.locator("#__drawer")).not_to_be_checked()
    else:
        sidebar = page.locator(".md-sidebar--primary").bounding_box()
        assert sidebar is not None and sidebar["x"] >= 0, sidebar
    page.locator(".md-content").scroll_into_view_if_needed()
    assert_no_horizontal_overflow(page)


@pytest.mark.parametrize("path", ["/", "/pypto/", "/pypto/zh/api/language/"])
def test_shared_assets_load_from_each_site(
    page: Page, site_origin: str, path: str
) -> None:
    responses: dict[str, int] = {}
    page.on(
        "response",
        lambda response: responses.update(
            {urlsplit(response.url).path: response.status}
        ),
    )
    visit(page, site_origin, path)
    assets = ["stylesheets/brand.css", "javascripts/preferences.js", "images/pypto.svg"]
    if path != "/":
        assets.append("stylesheets/docs.css")
    for asset in assets:
        matching = [
            status
            for url, status in responses.items()
            if url.endswith("/assets/" + asset)
        ]
        assert matching and all(200 <= status < 400 for status in matching), (
            asset,
            responses,
        )
    logo = page.locator(".brand img" if path == "/" else ".pypto-brand img")
    assert logo.evaluate("image => image.complete && image.naturalWidth > 0")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
