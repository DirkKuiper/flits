# AGENTS.md

Guidance for AI coding agents working in this repository or writing code that
uses FLITS. People should start with [README.md](README.md) and
[CONTRIBUTING.md](CONTRIBUTING.md); this file does not replace them.

## Using FLITS from code

FLITS measures fast radio bursts in dynamic spectra: SIGPROC `.fil`, PSRFITS
`.fits`/`.sf`, and CHIME/FRB HDF5 `.h5`. It starts from a burst you already
have; it does not search for bursts.

- One command per batch: `flits measure FILE... --dm DM` prints a JSON array
  (`--format jsonl` or `csv` also work). Check each record's `status` (`ok`,
  `low_sn`, `no_detection`, `error`) and `localization.warning_flags` before
  trusting a number. Exit code 0 means every burst was measured.
- One call per burst from Python: `flits.measure_burst(path, flits.MeasureOptions(dm=...))`.
- Full control: `flits.BurstSession.from_file(path, dm=...)`. Every action in
  the interface is a method on it.
- Keep masking on (the default) when localizing automatically. Unmasked
  interference can outshine the burst.
- Report uncertainties together with their `uncertainty_details[...]["classification"]`.
  A `statistical_only` flux or fluence uncertainty leaves out the calibration
  systematic. Pass `sefd_fractional_uncertainty` / `--sefd-uncertainty` when it
  is known.
- Times are in ms from the start of the file (the read window), frequencies in
  MHz, DM in pc cm^-3.

Docs for language models: <https://dirkkuiper.github.io/flits/llms.txt> (index)
and <https://dirkkuiper.github.io/flits/llms-full.txt> (everything). The same
guidance ships as an Agent Skill for agents in other projects:
`plugins/flits/skills/frb-analysis/SKILL.md`.

## Repository layout

| Path | Contents |
| --- | --- |
| `flits/session.py` | `BurstSession`: the analysis state and every operation on it. The interface, CLI, and Python API all go through it. |
| `flits/measure.py`, `flits/cli.py` | `flits measure`, `flits replay`, `flits demo`, and `flits serve`. |
| `flits/io/` | File readers, discovered through the `flits.readers` entry point. |
| `flits/analysis/` | Localization, DM optimization, widths, temporal/spectral, drift, polarization, fitting. |
| `flits/measurements.py`, `flits/models.py` | Measurement computation and the result dataclasses. |
| `flits/web/app.py`, `flits/web_static/` | FastAPI server and the plain-JavaScript interface (no build step). |
| `tests/` | pytest suite; `conftest.py` synthesizes every input file it needs. |
| `.claude-plugin/`, `plugins/flits/` | Claude Code plugin marketplace and the FLITS skill for coding agents; `tests/test_agent_guidance.py` checks the skill against the code. |
| `docs/`, `mkdocs.yml` | Documentation site. |
| `paper/` | JOSS manuscript. |

Directories such as `data/`, `tmp/`, `output/`, `analysis_outputs/`, and
`R147_*` hold local research material. They are ignored or untracked and are not
part of FLITS.

## Checks

Run these before proposing a change. CI runs them too:

```bash
python -m pytest -q                     # unit tests; excludes e2e and realdata
python -m ruff check . && python -m ruff format --check .
python -m mypy                          # type check
mkdocs build --strict                   # when docs/ or mkdocs.yml changed
python -m pytest tests/test_frontend_e2e.py -m e2e -q   # browser tests; need Playwright
```

`-m realdata` tests need the observatory data under `data/` or, for the demo
test, `FLITS_DEMO_DIR` pointing at the tutorial burst. They skip otherwise.

## Conventions

- Commit messages follow Conventional Commits (`feat(cli): ...`,
  `fix(io): ...`, `docs: ...`, `chore(deps): ...`). Release Please builds the
  version bump and changelog from them.
- Never edit `flits.__version__`, `CHANGELOG.md`, `.release-please-manifest.json`,
  or the version in `CITATION.cff` by hand; the release PR does that.
- New behaviour needs a test. Use the synthetic fixtures in `tests/conftest.py`
  rather than external data. Analysis changes should assert on the numbers.
- Every module must type check. Do not add modules to the mypy `ignore_errors`
  override list in `pyproject.toml`; it only shrinks.
- Keep `pyproject.toml` dependency ranges compatible. Exact pins belong in
  `requirements.txt`, which Dependabot maintains.
- Session snapshots are a versioned, portable format. Older snapshots must still
  load; `tests/test_historical_compatibility.py` guards this.
- Describe an uncertainty by what it is and what it includes, never by whether
  it may be published. Give every `UncertaintyDetail` a `basis` (how it was
  computed) and a `tooltip` (what it means, and how to add a missing term).
  `tests/test_uncertainty_language.py` enforces this; the kinds are listed in
  `docs/user-guide/uncertainties.md`.
