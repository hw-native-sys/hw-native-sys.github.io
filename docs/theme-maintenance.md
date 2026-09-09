# Shared documentation theme

The homepage and project documentation share the brand colors, logo, and color
preference in `docs-theme/`. Each project builds its own Material for MkDocs site,
including a local copy of the theme assets. Changes here reach a project only
after that project's pinned theme revision is updated and its site is deployed.

## Files and responsibilities

- `docs-theme/base.yml`: common appearance and the five-project directory.
- `docs-theme/constraints.txt`: the supported MkDocs and Material versions.
- `docs-theme/overrides/main.html`: extends Material's supported template blocks.
- `docs-theme/overrides/partials/`: the shared header and project selector.
- `docs-theme/overrides/assets/stylesheets/brand.css`: homepage/documentation
  tokens and the theme selector; `docs.css` contains Material-specific styling.
- `docs-theme/overrides/assets/javascripts/preferences.js`: a shared
  `pypto:color-scheme` preference with system, light, and dark modes. Unavailable
  storage falls back to system colors. Instant navigation reinitializes controls
  through Material's `document$` lifecycle.
- `.github/workflows/docs-theme.yml`: builds reference snapshots of all five
  projects against a candidate theme. It never deploys their documentation.

Keep navigation, language plugins, API plugins, link hooks, and content validation
in the consuming repository. Material configuration inheritance replaces lists;
it does not append to them. In particular, do not move a project's `nav` into this
repository: existing navigation checks read its root `mkdocs.yml` directly.

## Integrating a project

Record the full shared repository commit in `docs/theme-revision.txt` and ignore
`.site-theme/` in the project's `.gitignore`. Fetch that exact revision locally
and in the project's documentation workflow:

```bash
git init .site-theme
git -C .site-theme fetch --depth 1 \
  https://github.com/hw-native-sys/hw-native-sys.github.io.git \
  "$(cat docs/theme-revision.txt)"
git -C .site-theme checkout --detach FETCH_HEAD
```

Add the common constraint file to the project's documentation requirements. For
`docs/requirements.txt`, the relative include is:

```text
-c ../.site-theme/docs-theme/constraints.txt
```

For Toolkit's root-level `requirements-docs.txt`, use
`-c .site-theme/docs-theme/constraints.txt`. Resolve incompatible requirements
before integrating a project. The Toolkit reference snapshot caps Material below
9.7.2; the compatibility matrix explicitly tests a candidate 9.7.7 requirement
without changing that upstream repository's policy.

Use this configuration fragment, retaining the project's existing content and
plugins. Paths are resolved relative to the project's main `mkdocs.yml`:

```yaml
INHERIT: .site-theme/docs-theme/base.yml
site_name: PyPTO
site_url: https://www.pypto.ai/pypto/
theme:
  custom_dir: .site-theme/docs-theme/overrides
extra:
  pypto_project: pypto
```

The project identifier must match an entry in `extra.pypto_projects`. The shared
header keeps search within the current project. From a Chinese page, the project
selector links to another project's Chinese homepage when available and labels
English-only destinations explicitly. Project switching does not attempt to map
individual articles across repositories.

Common features are inherited when the local `theme.features` is omitted. A
project retaining optional features such as instant navigation must provide the
complete feature list, including the shared navigation features. Local plugin,
hook, and stylesheet lists likewise need to preserve every required entry.

Install only the documentation dependencies and build from that checkout:

```bash
python -m pip install -r docs/requirements.txt
mkdocs build --strict
mkdocs serve
```

Keep each project's existing documentation checks and artifact directory. This
workflow does not require compiling PyPTO, installing CANN, or running device
tests; API documentation is extracted from Python source.

## Validating and releasing

The compatibility workflow builds fixed snapshots of all five projects and
checks their rendered root and deep pages. Its PyPTO job also runs Chromium
checks against the homepage and the generated English/Chinese documentation.
To run those browser checks locally after building PyPTO:

```bash
python -m pip install -r tests/requirements.txt
python -m playwright install chromium
PYPTO_DOCS_SITE=/path/to/pypto/site python -m pytest tests/test_browser_theme.py -q
```

`tests/docs_theme_compat.py --help` describes the standalone candidate-build
and rendered-output checks used by CI.

The unchanged PyPTO reference build with Material 9.7.7 has two known i18n
limitations: optional alternate-page sitemap requests return 404, and a search
result in the other language can redirect to that language's homepage. The
browser suite checks navigation to results in the current language and permits
only those known optional sitemap responses. Shared assets, other HTTP errors,
and JavaScript errors remain checked. Revisit these baseline limitations when
upgrading Material or the i18n plugin.

1. Run the compatibility workflow and record the exact downstream source
   revisions. Update its reference snapshots deliberately as project APIs evolve.
2. Inspect generated English and Chinese homepages, deep articles, and API pages.
   Check the project selector, search, language switching, code copy, keyboard
   focus, and color preference across page and project changes.
3. Check desktop, tablet, and narrow mobile widths, both color schemes, and text
   enlargement. Code blocks and tables may scroll; the whole page must not.
4. Merge the shared theme, then update each consumer's revision file to the
   tested commit. A commit retained through a normal merge can remain pinned;
   after a squash, use and validate the resulting commit instead.
5. Merge each consumer independently, wait for its Pages deployment, and verify
   the deployed assets. The `pypto-docs-theme` metadata identifies this theme.

Roll back a consumer by restoring its previous revision file **and any dependency
changes required by that revision**, then rebuilding and deploying it. Reverting
only this repository does not change already deployed consumer sites.

Prefer small template extensions over copying Material's templates. When updating
Material, verify the header's drawer, search, language, and `data-md-component`
contracts as well as the appearance; a successful HTML build alone cannot check
the browser behavior.
