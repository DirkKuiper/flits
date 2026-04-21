# Publishing

FLITS now publishes through four automated paths:

- `release.yml` for release PRs, GitHub releases, PyPI, and stable GHCR images
- `publish-image.yml` for the rolling `edge` GHCR image
- `publish-package.yml` for manual TestPyPI or PyPI fallback publishing
- `docs.yml` for GitHub Pages documentation deployment

## One-time package setup

For Python package publishing, create matching GitHub environments in the
repository settings:

- `testpypi`
- `pypi`

Then configure pending publishers on both indexes with these values:

### TestPyPI pending publisher

- Project name: `flits`
- Owner: `DirkKuiper`
- Repository name: `flits`
- Workflow name: `publish-package.yml`
- Environment name: `testpypi`

### PyPI pending publisher

- Project name: `flits`
- Owner: `DirkKuiper`
- Repository name: `flits`
- Workflow name: `release.yml`
- Environment name: `pypi`

Also check the repository Actions settings:

- Workflow permissions: `Read and write permissions`
- Allow GitHub Actions to create and approve pull requests: enabled

If you want the manual PyPI fallback workflow to keep working with trusted
publishing, add a second PyPI publisher entry that points at
`publish-package.yml`.

## Normal development workflow

During normal development:

1. Make code changes.
2. Run the verification steps from [Testing](testing.md).
3. Commit using Conventional Commit prefixes where possible:
   - `fix:` for bug fixes
   - `feat:` for user-visible features
   - `docs:` for docs changes you want included in release notes
   - `refactor!:` or `feat!:` plus a `BREAKING CHANGE:` footer for breaking changes
4. Push to `main` through your normal PR flow.

You do not manually edit `flits.__version__` for routine development anymore.
Release Please updates it in the release PR.

## How releases work

`release.yml` runs on every push to `main`:

1. It updates or opens a release PR when there are releasable commits since the
   last release.
2. Merging that release PR updates `CHANGELOG.md`, bumps
   `flits.__version__`, creates the Git tag, and creates the GitHub release.
3. The same workflow then builds and publishes the PyPI package, publishes the
   stable GHCR image, and runs smoke tests against both artifacts.

While FLITS is still in the `0.x` series, breaking changes bump the minor
version instead of jumping to `1.0.0` automatically.

If you need to force a specific version, add a `Release-As: X.Y.Z` trailer to a
commit body.

## Edge container maintenance

`publish-image.yml` continuously publishes:

- `ghcr.io/dirkkuiper/flits:edge`
- `ghcr.io/dirkkuiper/flits:sha-<commit>`

It runs on pushes to `main`, on a weekly schedule, and manually. Use `edge`
only for testing snapshots; use `latest` or an exact release tag for actual
analysis environments.

## Publish to TestPyPI manually

Use the manual workflow when you want to verify packaging before cutting a real
release:

1. Open `Publish Python Package`.
2. Run the workflow with `repository=testpypi`.
3. Wait for the publish and smoke-test jobs to finish.

Verify the uploaded package in a clean environment:

```bash
python -m venv /tmp/flits-testpypi
source /tmp/flits-testpypi/bin/activate
python -m pip install --upgrade pip
python -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple flits
flits --help
```

## Manual PyPI fallback

If the automated release workflow is unavailable, you can still dispatch
`publish-package.yml` manually with `repository=pypi`.

## Deploy docs for free

The documentation site is built with MkDocs Material and deployed through
GitHub Pages. That setup is free for public repositories.

The `docs.yml` workflow builds the site on pull requests and deploys it from
`main`.

## Dependency and security automation

The repository also includes:

- `dependabot.yml` for weekly Python, Docker, and GitHub Actions updates
- a weekly scheduled `tests.yml` run to catch dependency drift
- `security.yml` for `pip-audit` and Trivy scanning

## Optional fitburst support

The PyPI package intentionally omits `fitburst` from its published dependency
metadata. Users who want the fitburst-backed scattering workflow can install it
after FLITS:

```bash
pip install "fitburst @ https://github.com/CHIMEFRB/fitburst/archive/3c76da8f9e3ec7bc21951ce1b4a26a0255096b69.tar.gz"
```
