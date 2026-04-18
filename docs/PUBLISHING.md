# Publishing FLITS

FLITS ships two release paths:

- `publish-package.yml` publishes Python distributions to TestPyPI or PyPI.
- `publish-image.yml` publishes the container image to GHCR.

The Python package workflow uses PyPI Trusted Publishing via GitHub Actions.

## One-time setup

Create matching GitHub environments in the repository settings:

- `testpypi`
- `pypi`

The `pypi` environment should require manual approval. `testpypi` can usually be
left without reviewers.

Then configure pending publishers on both indexes:

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
- Workflow name: `publish-package.yml`
- Environment name: `pypi`

## Release checklist

1. Update `flits.__version__` in `flits/__init__.py`.
2. Run the local verification steps from `docs/TESTING.md`.
3. Commit the release changes and push them to GitHub.

## Publish to TestPyPI

Use the GitHub Actions workflow manually:

1. Open `Publish Python Package`.
2. Run the workflow with `repository=testpypi`.
3. Wait for the `publish-testpypi` job to finish.

Verify the uploaded package in a clean virtual environment:

```bash
python -m venv /tmp/flits-testpypi
source /tmp/flits-testpypi/bin/activate
python -m pip install --upgrade pip
python -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple flits
flits --help
```

## Publish to PyPI

The normal production path is:

1. Create a Git tag such as `v0.1.0`.
2. Publish the matching GitHub release.
3. Let the `release` event trigger `publish-package.yml`.
4. Approve the `pypi` environment when GitHub requests it.

You can also dispatch the same workflow manually with `repository=pypi` if you
need to republish from an existing commit after a failed release process.

## Optional fitburst support

The PyPI package intentionally omits `fitburst` from its published dependency
metadata. `fitburst` is currently installed from a direct URL, and public Python
package indexes do not accept direct URL runtime dependencies in uploaded
package metadata.

Users who want the fitburst-backed scattering fit can install it after FLITS:

```bash
pip install "fitburst @ https://github.com/CHIMEFRB/fitburst/archive/3c76da8f9e3ec7bc21951ce1b4a26a0255096b69.tar.gz"
```
