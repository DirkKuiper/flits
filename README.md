# FLITS

Fast-Look Interactive Transient Suite.

Browser-based scientific software for interactive burst inspection, masking, and measurement on filterbank data.

## Run

```bash
.venv_test/bin/python -m flits --host 127.0.0.1 --port 8123
```

Then open `http://127.0.0.1:8123`.

For non-NRT data, use the `Generic Filterbank` preset and optionally provide an SEFD override if you want calibrated flux and fluence values.

## Code Structure

- `flits/settings.py`: observation presets and explicit overrides
- `flits/io/filterbank.py`: filterbank loading and Stokes-I extraction
- `flits/signal.py`: shared numerical utilities
- `flits/models.py`: typed metadata and measurement containers
- `flits/session.py`: interactive burst state and measurements
- `flits/web/app.py`: FastAPI server for the browser UI
- `tests/test_session_smoke.py`: smoke tests on a real local filterbank file

## Measurements

- Fluence (Jy ms), when an SEFD is provided
- Peak flux density (Jy), when an SEFD is provided
- Event duration (ms)
- Spectral extent (MHz)
- Peak MJD
- 1D Gaussian fits to selected burst regions
