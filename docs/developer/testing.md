# Testing

## Full local environment

Create a virtual environment and install the full test stack:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pip install -e .
```

`requirements-dev.txt` includes the optional `fitburst` dependency so the
scattering-fit tests exercise the full feature set.

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

## Targeted runs

Examples:

```bash
python -m pytest tests/test_web_api.py -q
python -m pytest tests/test_model_fitting.py -q
```
