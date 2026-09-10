"""Resolve, assemble, and guard the first centrally built documentation site."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

WEBSITE = "hw-native-sys/hw-native-sys.github.io"
SOURCE = "hw-native-sys/pypto"
WORKFLOW = ".github/workflows/docs.yml"
ORIGIN = "https://www.pypto.ai"
SHA = re.compile(r"[0-9a-f]{40}")
ROOT = Path(__file__).resolve().parents[1]


class GitHub:
    """Read the public repositories through the runner's GitHub CLI."""

    def get(self, endpoint: str) -> Any:
        result = subprocess.run(
            ["gh", "api", "--hostname", "github.com", "--method", "GET", endpoint],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return json.loads(result.stdout)

    def contains(self, repo: str, base: str, head: str) -> bool:
        if not SHA.fullmatch(base) or not SHA.fullmatch(head):
            raise ValueError("Comparison requires two full commit SHAs")
        if base == head:
            return True
        comparison = self.get(f"repos/{repo}/compare/{base}...{head}")
        return comparison["status"] in ("ahead", "identical")


def check_run(run: dict[str, Any], workflow_id: int) -> None:
    """Accept only a completed, successful upstream main documentation run."""
    if (
        run.get("repository", {}).get("full_name") != SOURCE
        or run.get("head_repository", {}).get("full_name") != SOURCE
        or run.get("head_branch") != "main"
        or run.get("event") not in ("push", "workflow_dispatch")
        or run.get("path") != WORKFLOW
        or run.get("workflow_id") != workflow_id
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or not SHA.fullmatch(str(run.get("head_sha", "")))
        or not isinstance(run.get("id"), int)
        or not isinstance(run.get("run_attempt"), int)
    ):
        raise ValueError("Expected a successful upstream PyPTO main Docs run")


def resolve(
    api: GitHub,
    website_sha: str,
    notification: dict[str, str],
) -> dict[str, Any]:
    """Validate a notification and freeze the newest eligible source snapshot."""
    if not SHA.fullmatch(website_sha):
        raise ValueError("Website revision must be a full commit SHA")
    workflow = api.get(f"repos/{SOURCE}/actions/workflows/docs.yml")
    workflow_id = workflow["id"]
    main_sha = api.get(f"repos/{SOURCE}/commits/main")["sha"]

    if any(notification.values()):
        if (
            notification.get("source_repository") != SOURCE
            or not SHA.fullmatch(notification.get("source_sha", ""))
            or not re.fullmatch(r"[1-9][0-9]*", notification.get("source_run_id", ""))
            or not re.fullmatch(
                r"[1-9][0-9]*", notification.get("source_run_attempt", "")
            )
        ):
            raise ValueError(
                "Notification requires the allowed repository, SHA, run and attempt"
            )
        notified = api.get(
            f"repos/{SOURCE}/actions/runs/{notification['source_run_id']}"
        )
        check_run(notified, workflow_id)
        if (
            notified["id"] != int(notification["source_run_id"])
            or notified["head_sha"] != notification["source_sha"]
            or notified["run_attempt"] != int(notification["source_run_attempt"])
            or not api.contains(SOURCE, notified["head_sha"], main_sha)
        ):
            raise ValueError(
                "Notification does not match the current upstream run and main history"
            )

    selected = None
    for page in range(1, 6):
        runs = api.get(
            f"repos/{SOURCE}/actions/workflows/{workflow_id}/runs"
            f"?branch=main&status=success&per_page=100&page={page}"
        )["workflow_runs"]
        for candidate in runs:
            try:
                check_run(candidate, workflow_id)
            except ValueError:
                continue
            if not api.contains(SOURCE, candidate["head_sha"], main_sha):
                continue
            # Re-read the run: it may have been rerun since the listing was fetched.
            current = api.get(f"repos/{SOURCE}/actions/runs/{candidate['id']}")
            try:
                check_run(current, workflow_id)
            except ValueError:
                continue
            if current["head_sha"] != candidate["head_sha"]:
                raise ValueError("Source run changed its commit identity")
            # GitHub lists runs newest first. Reruns retain the original run's
            # creation order; a pending newer commit is never substituted here.
            selected = current
            break
        if selected is not None or len(runs) < 100:
            break
    if selected is None:
        raise ValueError(
            "No successful main Docs snapshot found in the latest 500 successful runs"
        )

    inputs = {"website_sha": website_sha, "pypto_sha": selected["head_sha"]}
    return {
        "schema_version": 1,
        "scope": "pypto-pilot",
        "input_digest": hashlib.sha256(
            json.dumps(inputs, sort_keys=True).encode()
        ).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "website": {"repository": WEBSITE, "sha": website_sha},
        "projects": [
            {
                "id": "pypto",
                "repository": SOURCE,
                "sha": selected["head_sha"],
                "prefix": "/pypto/",
                "workflow_id": workflow_id,
                "run_id": selected["id"],
                "run_attempt": selected["run_attempt"],
                "run_url": selected["html_url"],
            }
        ],
    }


def read_manifest(path: Path) -> dict[str, Any]:
    """Reject unexpected projects before using a persisted release manifest."""
    manifest = json.loads(path.read_text())
    projects = manifest.get("projects", [])
    if (
        manifest.get("schema_version") != 1
        or manifest.get("scope") != "pypto-pilot"
        or manifest.get("website", {}).get("repository") != WEBSITE
        or not SHA.fullmatch(str(manifest.get("website", {}).get("sha", "")))
        or len(projects) != 1
        or projects[0].get("repository") != SOURCE
        or projects[0].get("id") != "pypto"
        or projects[0].get("prefix") != "/pypto/"
        or not SHA.fullmatch(str(projects[0].get("sha", "")))
    ):
        raise ValueError(
            "Manifest must describe exactly the PyPTO pilot and website revisions"
        )
    inputs = {
        "website_sha": manifest["website"]["sha"],
        "pypto_sha": projects[0]["sha"],
    }
    expected = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    if manifest.get("input_digest") != expected:
        raise ValueError("Manifest input digest does not match its revisions")
    return manifest


def verify_checkout(checkout: Path, expected_sha: str) -> None:
    actual = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual != expected_sha:
        raise ValueError(
            f"Checkout revision {actual} differs from expected {expected_sha}"
        )


def prepare(checkout: Path, manifest: dict[str, Any]) -> None:
    """Supply the website checkout's shared theme without changing project configuration."""
    verify_checkout(ROOT, manifest["website"]["sha"])
    verify_checkout(checkout, manifest["projects"][0]["sha"])
    target = checkout / ".site-theme" / "docs-theme"
    if target.exists():
        raise ValueError(f"Theme checkout already exists: {target}")
    shutil.copytree(ROOT / "docs-theme", target)


def assemble(checkout: Path, destination: Path, manifest: dict[str, Any]) -> None:
    """Package the homepage and the exact validated downstream site together."""
    verify_checkout(ROOT, manifest["website"]["sha"])
    verify_checkout(checkout, manifest["projects"][0]["sha"])
    site = checkout / "site"
    if not (site / "index.html").is_file() or not (site / "zh/index.html").is_file():
        raise ValueError("PyPTO English and Chinese build outputs are required")
    if any(path.is_symlink() for path in site.rglob("*")):
        raise ValueError("Pages artifacts must not contain symbolic links")
    if destination.exists():
        raise ValueError(f"Output directory already exists: {destination}")
    destination.mkdir(parents=True)
    shutil.copy2(ROOT / "index.html", destination / "index.html")
    shutil.copytree(
        ROOT / "docs-theme/overrides/assets",
        destination / "docs-theme/overrides/assets",
    )
    shutil.copytree(site, destination / "pypto")
    (destination / ".nojekyll").touch()
    manifest["renderer"] = {
        "mkdocs": version("mkdocs"),
        "material": version("mkdocs-material"),
    }
    (destination / "build-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )


def published_manifest() -> dict[str, Any] | None:
    request = urllib.request.Request(
        f"{ORIGIN}/build-manifest.json",
        headers={
            "Cache-Control": "no-cache",
            "User-Agent": "pypto-docs-publisher/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def guard(
    api: GitHub, manifest: dict[str, Any], previous: dict[str, Any] | None
) -> bool:
    """Reject retired inputs and confirm the source CI still succeeded before publication."""
    project = manifest["projects"][0]
    workflow_id = api.get(f"repos/{SOURCE}/actions/workflows/docs.yml")["id"]
    run = api.get(f"repos/{SOURCE}/actions/runs/{project['run_id']}")
    check_run(run, workflow_id)
    if (
        run["head_sha"] != project["sha"]
        or run["run_attempt"] != project["run_attempt"]
    ):
        raise ValueError("Source CI no longer matches the assembled artifact")
    website_head = api.get(f"repos/{WEBSITE}/commits/main")["sha"]
    if manifest["website"]["sha"] != website_head:
        raise ValueError(
            "Website main advanced; rebuild with the current publishing workflow"
        )
    source_head = api.get(f"repos/{SOURCE}/commits/main")["sha"]
    if not api.contains(SOURCE, project["sha"], source_head):
        raise ValueError("Selected source is no longer in main history")
    latest = resolve(api, manifest["website"]["sha"], {})
    if latest["projects"][0]["sha"] != project["sha"]:
        raise ValueError(
            "A newer successful Docs snapshot is available; rebuild before publishing"
        )
    if previous is None:
        return True
    if previous.get("scope") != "pypto-pilot" or len(previous.get("projects", [])) != 1:
        raise ValueError(
            "Existing site is not the PyPTO pilot; refusing to replace its project set"
        )
    if previous["projects"][0].get("repository") != SOURCE:
        raise ValueError("Existing publication has an unexpected source repository")
    if not api.contains(
        WEBSITE, previous["website"]["sha"], manifest["website"]["sha"]
    ):
        raise ValueError(
            "Candidate would replace a newer or unrelated website revision"
        )
    if not api.contains(SOURCE, previous["projects"][0]["sha"], project["sha"]):
        raise ValueError(
            "Candidate would replace a newer or unrelated documentation revision"
        )
    return previous.get("input_digest") != manifest["input_digest"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("resolve", "prepare", "assemble", "guard"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checkout", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "resolve":
        website_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        notification = {
            key: os.environ.get(key.upper(), "")
            for key in (
                "source_repository",
                "source_sha",
                "source_run_id",
                "source_run_attempt",
            )
        }
        manifest = resolve(GitHub(), website_sha, notification)
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(manifest, indent=2) + "\n")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                output.write(f"source_sha={manifest['projects'][0]['sha']}\n")
        print(json.dumps(manifest, indent=2))
        return
    manifest = read_manifest(args.manifest)
    if args.command == "guard":
        changed = guard(GitHub(), manifest, published_manifest())
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                output.write(f"changed={str(changed).lower()}\n")
        print(f"Publication inputs changed: {changed}")
    elif args.checkout is None:
        parser.error("--checkout is required")
    elif args.command == "prepare":
        prepare(args.checkout, manifest)
    elif args.output is None:
        parser.error("--output is required for assemble")
    else:
        assemble(args.checkout, args.output, manifest)


if __name__ == "__main__":
    main()
