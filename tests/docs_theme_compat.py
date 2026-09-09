"""Build inputs and rendered-output checks for fixed downstream snapshots.

Start from the theme checkout after installing docs-theme/constraints.txt::

    python tests/docs_theme_compat.py prepare --repo /path/to/downstream \
        --project pypto --expected-sha <full-sha>
    pip install -c docs-theme/constraints.txt \
        -r /path/to/downstream/requirements-theme-compat.txt
    cd /path/to/downstream
    mkdocs build --strict -f mkdocs.theme-compat.yml --site-dir site-theme
    python /path/to/theme/tests/docs_theme_compat.py check --site site-theme \
        --project pypto --page api/tile/index.html

Preparation writes disposable inputs next to the original mkdocs.yml so its
relative paths keep their meaning. The source config and requirements stay
unchanged. Toolkit's existing Material upper bound is replaced only in the
disposable requirements: that matrix entry evaluates an upgrade candidate.
"""

from __future__ import annotations

import argparse
import subprocess
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import yaml

THEME_ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ("pypto", "simpler", "pypto-lib", "pypto-serving", "pypto-tools")
CHINESE_PROJECTS = {"pypto", "pypto-tools"}
SHARED_ASSETS = {
    "style": ("assets/stylesheets/brand.css", "assets/stylesheets/docs.css"),
    "script": ("assets/javascripts/preferences.js",),
    "image": ("assets/images/pypto.svg",),
    "icon": ("assets/images/pypto.svg",),
}


def prepare(repo: Path, project: str, expected_sha: str) -> None:
    """Create a candidate config while retaining all project content settings."""
    repo = repo.resolve()
    actual_sha = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual_sha != expected_sha:
        raise ValueError(f"{project}: expected {expected_sha}, found {actual_sha}")
    config = yaml.safe_load((repo / "mkdocs.yml").read_text())
    shared = yaml.safe_load((THEME_ROOT / "docs-theme/base.yml").read_text())
    theme = config.setdefault("theme", {})
    original_features = theme.get("features", [])
    for key in shared["theme"]:
        theme.pop(key, None)
    theme["custom_dir"] = str(THEME_ROOT / "docs-theme/overrides")
    theme["features"] = list(
        dict.fromkeys(shared["theme"]["features"] + original_features)
    )
    config["INHERIT"] = str(THEME_ROOT / "docs-theme/base.yml")
    config["site_url"] = f"https://www.pypto.ai/{project}/"
    config["extra_css"] = list(
        dict.fromkeys(shared["extra_css"] + config.get("extra_css", []))
    )
    extra = config.setdefault("extra", {})
    for key in shared["extra"]:
        extra.pop(key, None)
    extra["pypto_project"] = project
    (repo / "mkdocs.theme-compat.yml").write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    )

    requirement_path = (
        "requirements-docs.txt" if project == "pypto-tools" else "docs/requirements.txt"
    )
    requirements = (repo / requirement_path).read_text()
    if project == "pypto-tools":
        old = "mkdocs-material>=9.5,<9.7.2"
        if requirements.count(old) != 1:
            raise ValueError(
                "Toolkit's pinned snapshot no longer has the expected Material constraint"
            )
        requirements = requirements.replace(old, "mkdocs-material==9.7.7")
        print(
            "Toolkit: testing Material 9.7.7; original <9.7.2 constraint is unchanged"
        )
    (repo / "requirements-theme-compat.txt").write_text(requirements)
    print(f"Prepared {project} snapshot {actual_sha}")


class RenderedPage(HTMLParser):
    """Collect public theme output without importing any theme implementation."""

    def __init__(self) -> None:
        super().__init__()
        self.language = ""
        self.markers: list[str] = []
        self.assets: list[tuple[str, str]] = []
        self.projects: list[dict[str, str]] = []
        self.menu_depth = 0
        self.anchor: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if tag == "html":
            self.language = data.get("lang", "") or ""
        if tag == "meta" and data.get("name") == "pypto-docs-theme":
            self.markers.append(data.get("content", "") or "")
        if tag == "script" and data.get("src"):
            self.assets.append(("script", data["src"]))
        if tag == "img" and data.get("src"):
            self.assets.append(("image", data["src"]))
        if tag == "link" and data.get("href"):
            rel = (data.get("rel", "") or "").split()
            if "stylesheet" in rel:
                self.assets.append(("style", data["href"]))
            elif "icon" in rel:
                self.assets.append(("icon", data["href"]))
        if tag == "nav":
            if self.menu_depth:
                self.menu_depth += 1
            elif "pypto-project-menu" in (data.get("class", "") or "").split():
                self.menu_depth = 1
        if tag == "a" and self.menu_depth:
            self.anchor = {
                "href": data.get("href", "") or "",
                "current": data.get("aria-current", "") or "",
                "text": "",
            }
            if "pypto-project-home" not in (data.get("class", "") or "").split():
                self.projects.append(self.anchor)

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav" and self.menu_depth:
            self.menu_depth -= 1
        if tag == "a":
            self.anchor = None

    def handle_data(self, data: str) -> None:
        if self.anchor is not None:
            self.anchor["text"] += data


def check_page(site: Path, page: Path, project: str, chinese: bool) -> None:
    """Check branding, usable project links, and resolved assets on one page."""
    parsed = RenderedPage()
    parsed.feed((site / page).read_text())
    assert parsed.markers == ["1"], f"{page}: shared theme marker missing or duplicated"
    assert parsed.language.startswith("zh") == chinese, (
        f"{page}: incorrect rendered language"
    )
    assert len(parsed.projects) == 5, f"{page}: expected exactly five project links"
    expected_links = {
        f"https://www.pypto.ai/{name}/"
        + ("zh/" if chinese and name in CHINESE_PROJECTS else "")
        for name in PROJECTS
    }
    assert {link["href"] for link in parsed.projects} == expected_links, (
        f"{page}: project URLs differ"
    )
    current_url = f"https://www.pypto.ai/{project}/" + ("zh/" if chinese else "")
    current = [link["href"] for link in parsed.projects if link["current"] == "true"]
    assert current == [current_url], f"{page}: current-project marker is incorrect"
    if chinese:
        for link in parsed.projects:
            if not link["href"].endswith("/zh/"):
                assert "English" in link["text"], (
                    f"{page}: English fallback is not labelled"
                )

    site_url = f"https://www.pypto.ai/{project}/"
    page_url = urljoin(site_url, page.as_posix())
    local_assets: Counter[tuple[str, str]] = Counter()
    for kind, url in parsed.assets:
        resolved = urlsplit(urljoin(page_url, url))
        if (
            resolved.scheme not in ("http", "https")
            or resolved.netloc != "www.pypto.ai"
        ):
            continue
        prefix = f"/{project}/"
        assert resolved.path.startswith(prefix), f"{page}: asset escapes project: {url}"
        relative = unquote(resolved.path.removeprefix(prefix))
        target = (site / relative).resolve()
        assert target.is_relative_to(site), (
            f"{page}: asset escapes output directory: {url}"
        )
        assert target.is_file(), f"{page}: local asset does not exist: {url}"
        local_assets[kind, relative] += 1
    # The bilingual PyPTO baseline already repeats _mkdocstrings.css. This
    # theme's contract requires its own assets exactly once; plugin-owned
    # assets are checked for existence without changing upstream behavior.
    for kind, assets in SHARED_ASSETS.items():
        for asset in assets:
            count = local_assets[kind, asset]
            assert count >= 1, f"{page}: missing shared {kind}: {asset}"
            if kind != "image":
                assert count == 1, f"{page}: duplicate shared {kind}: {asset}"
    print(f"Validated {project}: {page}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--repo", type=Path, required=True)
    prepare_parser.add_argument("--project", choices=PROJECTS, required=True)
    prepare_parser.add_argument("--expected-sha", required=True)
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--site", type=Path, required=True)
    check_parser.add_argument("--project", choices=PROJECTS, required=True)
    check_parser.add_argument(
        "--page", type=Path, required=True, help="A representative deep HTML page"
    )
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.repo, args.project, args.expected_sha)
        return
    assert args.page != Path("index.html"), (
        "Select a deep page as well as the home page"
    )
    site = args.site.resolve()
    for page in (Path("index.html"), args.page):
        assert not page.is_absolute() and ".." not in page.parts, (
            "Page must stay inside the site"
        )
        check_page(site, page, args.project, chinese=False)
        if args.project in CHINESE_PROJECTS:
            check_page(site, Path("zh") / page, args.project, chinese=True)


if __name__ == "__main__":
    main()
