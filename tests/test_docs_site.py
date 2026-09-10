"""Check the trust boundary and version consistency of central publication."""

import copy
import io
import json
import urllib.error

import pytest

from scripts import docs_site as site

OLD = "a" * 40
NEW = "b" * 40
WEB = "c" * 40
LATER_WEB = "d" * 40


def docs_run(sha=OLD, run_id=10, **changes):
    run = {
        "repository": {"full_name": site.SOURCE},
        "head_repository": {"full_name": site.SOURCE},
        "head_branch": "main",
        "head_sha": sha,
        "event": "push",
        "path": site.WORKFLOW,
        "workflow_id": 100,
        "id": run_id,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
        "html_url": f"https://github.com/{site.SOURCE}/actions/runs/{run_id}",
    }
    return run | changes


class FakeGitHub:
    def __init__(self, runs=None):
        self.runs = runs if runs is not None else [docs_run()]
        self.current = {run["id"]: copy.deepcopy(run) for run in self.runs}
        self.main = NEW
        self.website_main = WEB
        self.history = {(site.SOURCE, OLD, NEW), (site.WEBSITE, WEB, LATER_WEB)}

    def get(self, endpoint):
        if endpoint == f"repos/{site.SOURCE}/actions/workflows/docs.yml":
            return {"id": 100}
        if endpoint == f"repos/{site.SOURCE}/commits/main":
            return {"sha": self.main}
        if endpoint == f"repos/{site.WEBSITE}/commits/main":
            return {"sha": self.website_main}
        if endpoint.startswith(f"repos/{site.SOURCE}/actions/workflows/100/runs?"):
            return {"workflow_runs": copy.deepcopy(self.runs)}
        if endpoint.startswith(f"repos/{site.SOURCE}/actions/runs/"):
            return copy.deepcopy(self.current[int(endpoint.rsplit("/", 1)[1])])
        raise AssertionError(f"Unexpected API request: {endpoint}")

    def contains(self, repo, base, head):
        return base == head or (repo, base, head) in self.history


def notification(sha=OLD, run_id="10", attempt="1"):
    return {
        "source_repository": site.SOURCE,
        "source_sha": sha,
        "source_run_id": run_id,
        "source_run_attempt": attempt,
    }


def test_successful_a_is_selected_while_main_b_is_pending():
    api = FakeGitHub(
        [docs_run(NEW, 11, status="in_progress", conclusion=None), docs_run()]
    )
    manifest = site.resolve(api, WEB, notification())
    assert manifest["projects"][0]["sha"] == OLD
    assert manifest["projects"][0]["run_id"] == 10


def test_late_notification_does_not_select_old_commit():
    api = FakeGitHub([docs_run(NEW, 11), docs_run()])
    assert site.resolve(api, WEB, notification())["projects"][0]["sha"] == NEW


@pytest.mark.parametrize(
    "change",
    [
        {"conclusion": "failure"},
        {"conclusion": "cancelled"},
        {"status": "in_progress"},
        {"event": "pull_request"},
        {"event": "pull_request_target"},
        {"head_branch": "feature"},
        {"head_repository": {"full_name": "contributor/pypto"}},
        {"repository": {"full_name": "contributor/pypto"}},
        {"workflow_id": 101},
        {"path": ".github/workflows/ci.yml"},
    ],
)
def test_untrusted_or_unsuccessful_run_cannot_notify(change):
    with pytest.raises(ValueError, match="successful upstream"):
        site.resolve(FakeGitHub([docs_run(**change)]), WEB, notification())


@pytest.mark.parametrize(
    "change",
    [
        {"source_repository": "contributor/pypto"},
        {"source_sha": "main"},
        {"source_run_id": "10/../11"},
        {"source_run_attempt": ""},
        {"source_run_id": "0"},
    ],
)
def test_malformed_notification_is_rejected_before_run_lookup(change):
    with pytest.raises(ValueError, match="Notification requires"):
        site.resolve(FakeGitHub(), WEB, notification() | change)


def test_mismatched_attempt_is_rejected():
    with pytest.raises(ValueError, match="does not match"):
        site.resolve(FakeGitHub(), WEB, notification(attempt="2"))


def test_notification_must_belong_to_current_main_history():
    api = FakeGitHub()
    api.history.clear()
    with pytest.raises(ValueError, match="main history"):
        site.resolve(api, WEB, notification())


def test_rerun_in_progress_after_listing_is_not_selected():
    api = FakeGitHub([docs_run(NEW, 11), docs_run()])
    api.current[11]["status"] = "in_progress"
    assert site.resolve(api, WEB, {})["projects"][0]["sha"] == OLD


def test_no_eligible_ci_never_falls_back_to_main_head():
    api = FakeGitHub([docs_run(conclusion="failure")])
    with pytest.raises(ValueError, match="No successful"):
        site.resolve(api, WEB, {})


def test_retry_of_same_source_has_same_publication_identity():
    first = site.resolve(FakeGitHub(), WEB, {})
    again = site.resolve(FakeGitHub([docs_run(run_attempt=2)]), WEB, {})
    assert first["input_digest"] == again["input_digest"]


def test_manifest_rejects_extra_project_and_modified_digest(tmp_path):
    manifest = site.resolve(FakeGitHub(), WEB, {})
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    assert site.read_manifest(path)["projects"][0]["sha"] == OLD
    manifest["projects"].append(copy.deepcopy(manifest["projects"][0]))
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="exactly"):
        site.read_manifest(path)
    manifest["projects"].pop()
    manifest["projects"][0]["sha"] = NEW
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="digest"):
        site.read_manifest(path)


def test_initial_publication_and_unchanged_inputs():
    api = FakeGitHub()
    manifest = site.resolve(api, WEB, {})
    assert site.guard(api, manifest, None)
    assert not site.guard(api, manifest, copy.deepcopy(manifest))


def test_old_source_cannot_replace_newer_publication():
    api = FakeGitHub()
    old = site.resolve(api, WEB, {})
    newer = site.resolve(FakeGitHub([docs_run(NEW, 11)]), WEB, {})
    with pytest.raises(ValueError, match="newer or unrelated documentation"):
        site.guard(api, old, newer)


def test_new_website_main_rejects_old_workflow_replay():
    api = FakeGitHub()
    manifest = site.resolve(api, WEB, {})
    api.website_main = LATER_WEB
    with pytest.raises(ValueError, match="Website main advanced"):
        site.guard(api, manifest, None)


def test_publication_rechecks_completed_source_attempt():
    api = FakeGitHub()
    manifest = site.resolve(api, WEB, {})
    api.current[10]["run_attempt"] = 2
    with pytest.raises(ValueError, match="no longer matches"):
        site.guard(api, manifest, None)


def test_new_success_during_build_rejects_stale_artifact_even_without_cached_manifest():
    api = FakeGitHub()
    manifest = site.resolve(api, WEB, {})
    api.runs.insert(0, docs_run(NEW, 11))
    api.current[11] = docs_run(NEW, 11)
    with pytest.raises(ValueError, match="newer successful Docs snapshot"):
        site.guard(api, manifest, None)


def test_pilot_cannot_overwrite_a_later_multi_project_release():
    api = FakeGitHub()
    manifest = site.resolve(api, WEB, {})
    previous = copy.deepcopy(manifest)
    previous["projects"].append({"id": "simpler"})
    with pytest.raises(ValueError, match="project set"):
        site.guard(api, manifest, previous)


@pytest.fixture
def output_sources(tmp_path, monkeypatch):
    root = tmp_path / "website"
    source = tmp_path / "source"
    assets = root / "docs-theme/overrides/assets"
    assets.mkdir(parents=True)
    (assets / "brand.css").write_text("body {}")
    (root / "index.html").write_text(
        '<link href="docs-theme/overrides/assets/brand.css">'
    )
    (root / "internal-source.py").write_text("not part of the public website")
    (source / "site/zh").mkdir(parents=True)
    (source / "site/index.html").write_text('<html lang="en">English</html>')
    (source / "site/zh/index.html").write_text('<html lang="zh">Chinese</html>')
    (source / "site/search").mkdir()
    (source / "site/search/search_index.json").write_text('{"docs": []}')
    monkeypatch.setattr(site, "ROOT", root)
    monkeypatch.setattr(site, "verify_checkout", lambda *_: None)
    monkeypatch.setattr(site, "version", lambda _: "test-version")
    return source, tmp_path / "public"


def test_complete_artifact_preserves_paths_and_records_source(output_sources):
    source, output = output_sources
    manifest = site.resolve(FakeGitHub(), WEB, {})
    site.assemble(source, output, manifest)
    assert (output / "index.html").is_file()
    assert (output / "docs-theme/overrides/assets/brand.css").is_file()
    assert (output / "pypto/zh/index.html").is_file()
    assert (output / "pypto/search/search_index.json").is_file()
    assert not (output / "internal-source.py").exists()
    assert (
        site.read_manifest(output / "build-manifest.json")["projects"][0]["sha"] == OLD
    )


def test_missing_chinese_output_prevents_publication(output_sources):
    source, output = output_sources
    (source / "site/zh/index.html").unlink()
    with pytest.raises(ValueError, match="English and Chinese"):
        site.assemble(source, output, site.resolve(FakeGitHub(), WEB, {}))
    assert not output.exists()


def test_symlinks_cannot_escape_into_published_artifact(output_sources):
    source, output = output_sources
    (source / "site/outside").symlink_to(source.parent)
    with pytest.raises(ValueError, match="symbolic"):
        site.assemble(source, output, site.resolve(FakeGitHub(), WEB, {}))
    assert not output.exists()


def test_publication_reader_identifies_itself_and_reads_json(monkeypatch):
    def open_manifest(request, timeout):
        assert request.full_url == "https://www.pypto.ai/build-manifest.json"
        assert request.get_header("User-agent") == "pypto-docs-publisher/1.0"
        return io.StringIO('{"scope": "pypto-pilot"}')

    monkeypatch.setattr(site.urllib.request, "urlopen", open_manifest)
    assert site.published_manifest() == {"scope": "pypto-pilot"}


@pytest.mark.parametrize("code", [403, 404])
def test_only_a_missing_manifest_allows_first_publication(monkeypatch, code):
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://www.pypto.ai/build-manifest.json", code, "test", {}, None
        )

    monkeypatch.setattr(site.urllib.request, "urlopen", fail)
    if code == 404:
        assert site.published_manifest() is None
    else:
        with pytest.raises(urllib.error.HTTPError):
            site.published_manifest()
