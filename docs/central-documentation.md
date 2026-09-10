# Central documentation pilot

The website repository can build a successful PyPTO documentation snapshot and
package it together with the homepage. The other four project sites retain
their existing publishing workflows. Full-site search and their migration are
separate follow-up changes; this pilot retains PyPTO's existing bilingual search.

## Source and artifact contract

`.github/workflows/docs-site.yml` selects a completed, successful run of
`hw-native-sys/pypto`'s `.github/workflows/docs.yml` on `main`. The selected commit
must still belong to upstream main history. A newer commit whose documentation
CI has not succeeded is not substituted for it.

The central workflow uses its own checkout's `docs-theme/`, runs PyPTO's
documentation checks and strict build, and uploads `docs-site-pilot`. The
artifact contains the homepage, its existing assets, `/pypto/`, and
`build-manifest.json`. The manifest records both source revisions and the
successful source run. Source links use the selected PyPTO SHA. No compiler,
CANN installation, or device tests are required.

## CI notifications

PyPTO's `Notify central documentation` workflow runs after its Docs workflow
completes successfully on upstream `main`. It dispatches `docs-site.yml` on the
website's `main` with `source_repository`, `source_sha`, `source_run_id`, and
`source_run_attempt`. The receiver verifies these claims through GitHub's API
before selecting the latest eligible snapshot. Manual runs leave all four
inputs empty.

The notification workflow uses a GitHub App installation token scoped to this
website repository with **Actions: write**. Its App ID and private key are
provided to PyPTO through `DOCS_SITE_APP_ID` (Actions variable) and
`DOCS_SITE_APP_PRIVATE_KEY` (Actions secret). A source repository's own
`GITHUB_TOKEN` does not grant cross-repository write access.

The notification listener must be merged into PyPTO's default branch, and the
receiving workflow must be present on the website's default branch, before this
event path can run. A successful notification means the build was requested;
the central workflow and deployed manifest establish publication success.

## Activation and ownership

All switches are repository Actions variables and default to disabled:

| Repository | Variable | Effect when enabled |
| --- | --- | --- |
| Website | `DOCS_SITE_ENABLED=true` | Enable automatic push and scheduled pilot builds; PR and manual builds already run |
| PyPTO | `DOCS_SITE_NOTIFY_ENABLED=true` | Send notifications after successful upstream Docs runs |
| Website | `DOCS_SITE_PUBLISH=true` | Permit the central main workflow to deploy its validated artifact |
| PyPTO | `DOCS_SITE_PUBLISHER=central` | Stop the original Pages upload/deploy while preserving document validation |

First enable artifact-only builds and notifications after configuring the App.
Before enabling publication, verify the root-versus-project Pages routing
handoff with an isolated route, select GitHub Actions as the website's Pages
source, and preserve `www.pypto.ai` in its Pages settings. Coordinate PyPTO's
publishing switch and Pages deactivation with that handoff. Changing its workflow
variable alone does not remove the existing project Pages deployment.

The production workflow is serialized separately from PR checks. Immediately
before deployment it rejects a stale website workflow, superseded PyPTO source,
changed source CI attempt, or an existing publication containing additional
projects. Unchanged input revisions skip deployment. The scheduled check at
minutes 7 and 37 uses the same successful-CI selection as notifications.

Both default-branch workflow availability and App configuration are prerequisites
for the live notification test. Unit tests and artifact builds do not establish
that the App has been installed or that Pages routing has been handed over.
