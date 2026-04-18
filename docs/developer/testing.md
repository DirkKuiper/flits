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
python -m pytest tests/test_fit_scattering.py -q
```
