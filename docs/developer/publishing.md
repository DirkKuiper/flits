# Publishing

FLITS currently has three automated publication paths:

- `publish-package.yml` for TestPyPI and PyPI
- `publish-image.yml` for the GHCR container image
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
- Workflow name: `publish-package.yml`
- Environment name: `pypi`

## Release checklist

1. Update `flits.__version__` in `flits/__init__.py`.
2. Run the verification steps from [Testing](testing.md).
3. Commit the release changes and push them to GitHub.

## Publish to TestPyPI

Use the GitHub Actions workflow manually:

1. Open `Publish Python Package`.
2. Run the workflow with `repository=testpypi`.
3. Wait for the `publish-testpypi` job to finish.

Verify the uploaded package in a clean environment:

```bash
python -m venv /tmp/flits-testpypi
source /tmp/flits-testpypi/bin/activate
python -m pip install --upgrade pip
python -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple flits
flits --help
```

## Publish to PyPI

The simplest path is:

1. Create a Git tag such as `v0.1.1`.
2. Publish the matching GitHub release.
3. Let the `release` event trigger `publish-package.yml`.
4. Approve the `pypi` environment when GitHub requests it.

You can also dispatch the same workflow manually with `repository=pypi`.

## Deploy docs for free

The documentation site is built with MkDocs Material and deployed through
GitHub Pages. That setup is free for public repositories.

The `docs.yml` workflow builds the site on pull requests and deploys it from
`main`.

## Optional fitburst support

The PyPI package intentionally omits `fitburst` from its published dependency
metadata. Users who want the fitburst-backed scattering workflow can install it
after FLITS:

```bash
pip install "fitburst @ https://github.com/CHIMEFRB/fitburst/archive/3c76da8f9e3ec7bc21951ce1b4a26a0255096b69.tar.gz"
```
