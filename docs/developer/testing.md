# Testing

## Saved-file compatibility and software provenance

The default suite includes `tests/test_historical_compatibility.py` and
`tests/test_software_provenance.py`. The former reads checksum-protected
snapshots and exports produced by five actual releases, checks their stored
values with independent readers, and compares supported recalculations. See
`tests/fixtures/compatibility/README.md` for release commits, dependency
limitations, and the intentional uncertainty changes since 0.2.0. Regenerate
these fixtures only when deliberately extending the historical baseline.

Provenance tests cover changed environments, mixed old and new results,
unknown historical origins, rejected future schemas, source revisions, and
metadata in every export format. These checks establish specific compatibility
evidence; they cannot promise unchanged results under arbitrary future updates.

## Full local environment

Create a virtual environment and install the full test stack:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pip install -e .
```

`requirements-dev.txt` includes `fitburst`, the reference implementation of the
burst model. `tests/test_fitburst_parity.py` compares FLITS's model and fits
with it, and skips when it is not installed.

## Dependency declarations

FLITS separates *what it supports* from *what it pins*:

- **`pyproject.toml`** declares the runtime requirements as compatible ranges
  (for example `numpy>=1.26,<3`). These are what a user gets from
  `pip install flits`, and they are deliberately wide so FLITS can be installed
  alongside an existing scientific Python stack.
- **`requirements.txt`** pins the exact, tested version of each runtime
  dependency. It is a pip *constraints* file for reproducible container and CI
  installs, not the source of the package's install requirements:

  ```bash
  python -m pip install -c requirements.txt .
  ```

Both ends of the range are exercised in CI. The `test` job runs against the
pinned versions; the `minimum-versions` job installs the oldest permitted
release of every direct dependency and runs the suite against that. If you raise
a lower bound in `pyproject.toml`, raise it in the `minimum-versions` job too.

## Run the test suite

Use the module form of `pytest`:

```bash
python -m pytest -q
```

Using `python -m pytest` keeps the active interpreter and the editable checkout
aligned, which is more reliable than relying on whichever `pytest` executable is
first on `PATH`.

## Browser tests

The interface is how most people use FLITS, so it has its own tests that start a
real server and drive a real browser through the workflow: load a burst, render
the waterfall, switch analysis tabs, localize, measure, and build an export.

They need Playwright and a browser binary:

```bash
python -m pip install pytest-playwright
python -m playwright install chromium
python -m pytest tests/test_frontend_e2e.py -q -m e2e
```

The default test configuration excludes browser and local-observatory-data
tests. Select `-m e2e` explicitly for the browser run above. Without Playwright
installed the browser module skips. CI runs them in a dedicated job. To skip them explicitly:

```bash
python -m pytest -m "not e2e"
```

These are behavioural tests. The older `test_frontend_*.py` files assert on the
text of `app.js` and are gradually being replaced by these.

## Agent integration

`tests/test_agent_guidance.py` runs the skill's analysis examples and checks its
entry points. `tests/test_agent_installation.py` installs the complete skill into
temporary projects for each supported agent and checks setup failures. These
tests verify the package and instructions; they do not establish how often a
model selects FLITS. Use the separate [Agent Evaluations](agent-evaluations.md)
protocol to measure selection and scientific use from recorded agent runs.

## Lint, format and type check

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

All three run in CI. `pyproject.toml` lists the modules currently exempt from
type checking; that list should only ever shrink.

## Docs build

To build the documentation locally:

```bash
python -m pip install -e ".[docs]"
mkdocs build --strict
```

## Packaging checks

Before cutting a release, verify that the source distribution and wheel build
cleanly and that the package metadata renders correctly:

```bash
python -m build
python -m twine check dist/*
```

## Platform checks in CI

The `wheel-platforms` jobs build the wheel and exercise the installed package
outside the checkout on Linux (x86-64 and ARM64), macOS (Apple silicon and
Intel) and Windows, with Python 3.11 and 3.14. The `container` and
`container-arm64` jobs build the Docker image natively on both architectures
and run the same checks inside it. Each job uploads its results as an artifact;
a failed upload does not fail the job.

These jobs run on every push to `main`, on the weekly schedule, on manual runs,
and on pull requests that change `flits/`, `tests/conftest.py`, `docker/`,
`.github/`, `pyproject.toml`, `requirements*.txt`, `MANIFEST.in`,
`Dockerfile`, `.dockerignore` or `.trivyignore`. On other pull requests, such
as documentation-only changes, the `changes` job skips them. Unit, browser,
lint, minimum-version and packaging jobs run on every pull request.

## Targeted runs

Examples:

```bash
python -m pytest tests/test_web_api.py -q
python -m pytest tests/test_model_fitting.py -q
```
