# burst_analyzer

Browser-based scientific software for interactive burst inspection, masking, and measurement on filterbank data.

## Run

```bash
.venv_test/bin/python -m burst_analyzer --host 127.0.0.1 --port 8123
```

Then open `http://127.0.0.1:8123`.

For non-NRT data, use the `Generic Filterbank` preset and optionally provide an SEFD override if you want calibrated flux and fluence values.

## Code Structure

- `burst_analyzer/settings.py`: observation presets and explicit overrides
- `burst_analyzer/io/filterbank.py`: filterbank loading and Stokes-I extraction
- `burst_analyzer/signal.py`: shared numerical utilities
- `burst_analyzer/models.py`: typed metadata and measurement containers
- `burst_analyzer/session.py`: interactive burst state and measurements
- `burst_analyzer/web/app.py`: FastAPI server for the browser UI
- `tests/test_session_smoke.py`: smoke tests on a real local filterbank file

## Measurements

- Fluence (Jy ms), when an SEFD is provided
- Peak flux density (Jy), when an SEFD is provided
- Event duration (ms)
- Spectral extent (MHz)
- Peak MJD
- 1D Gaussian fits to selected burst regions
