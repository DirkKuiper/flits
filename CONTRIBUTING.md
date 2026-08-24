# Contributing to FLITS

Thanks for your interest in FLITS. Bug reports, format support, analysis
improvements and documentation fixes are all welcome.

## Getting support or asking a question

- **Questions about using FLITS** — open a
  [GitHub Discussion](https://github.com/DirkKuiper/flits/discussions), or a
  [question issue](https://github.com/DirkKuiper/flits/issues/new/choose) if
  Discussions is unavailable.
- **Documentation** — the full user and developer guide lives at
  [dirkkuiper.github.io/flits](https://dirkkuiper.github.io/flits/).

## Reporting a problem

Open an issue using the
[bug report template](https://github.com/DirkKuiper/flits/issues/new/choose).
The template asks for the FLITS version (`flits --version`), Python version,
operating system, input format, and the steps that reproduce the problem.

A burst that FLITS misreads is one of the most useful reports we can get. If the
data can be shared, a small cut-out of the file makes the fix much faster. If it
cannot, the header dump from `/api/detect` (or the format detection panel in the
interface) is usually enough.

For anything with security impact, follow [SECURITY.md](SECURITY.md) instead of
opening a public issue.

## Development setup

```bash
git clone https://github.com/DirkKuiper/flits.git
cd flits
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pip install -e .
```

`requirements-dev.txt` pulls in the optional `fitburst` dependency so the
model-fitting tests run, plus `pytest`, `httpx`, `ruff` and `mypy`.

## Before you open a pull request

```bash
python -m pytest -q          # full suite; must pass
python -m ruff check .       # lint
python -m ruff format --check .
python -m mypy flits         # type check
mkdocs build --strict        # only if you touched docs/
```

CI runs all of these plus a package build, a container build, and a
`minimum-versions` job that installs the oldest dependency versions the project
claims to support. See [docs/developer/testing.md](docs/developer/testing.md)
for details.

### Tests

New behaviour needs a test. The suite is entirely self-contained — every
fixture in `tests/conftest.py` synthesizes its own SIGPROC, PSRFITS or CHIME
HDF5 file in a temporary directory, so tests never depend on external data.
Please keep it that way.

Analysis changes that affect numbers should assert on the numbers, not just on
the shape of the result.

### Commit messages

FLITS uses [Conventional Commits](https://www.conventionalcommits.org/). The
release automation reads them to decide version bumps and to build the
changelog, so the prefix matters:

```
feat(analysis): add weighted RM synthesis
fix(io): correct CHIME catalog time units
docs: document automatic burst localization
test: cover the export artifact download path
chore(deps): bump numpy
```

Common scopes: `analysis`, `io`, `web`, `polarization`, `deps`, `cli`.
A `!` after the scope (or a `BREAKING CHANGE:` footer) triggers a major bump.

## Adding support for a new file format

FLITS discovers readers through `importlib` entry points, so a new format does
not require changing FLITS itself. See
[Custom Readers](https://dirkkuiper.github.io/flits/developer/custom-readers/)
for the reader protocol and a worked example. If the format is broadly useful,
a pull request adding it to `flits/io/` is welcome too.

## Scope

FLITS is an interactive analysis tool for individual bursts, plus a scriptable
Python API and a headless replay path for reproducing a saved session. It is
not a search or detection pipeline — candidate generation is out of scope, and
FLITS starts from a burst you already have.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By
participating you are expected to uphold it.

## Licence

FLITS is GPL-3.0-only. Contributions are accepted under the same licence.
