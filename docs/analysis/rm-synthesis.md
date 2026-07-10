# Rotation-Measure Synthesis

FLITS provides weighted one-dimensional RM synthesis for channelized Stokes
Q/U spectra. The result contains the complex dirty Faraday spectrum, RMSF,
peak Faraday depth, nominal peak uncertainty, and wavelength-coverage limits.

```python
from flits.analysis import run_rm_synthesis

result = run_rm_synthesis(
    freqs_mhz=freqs_mhz,
    stokes_q=stokes_q,
    stokes_u=stokes_u,
    sigma_q=sigma_q,
    sigma_u=sigma_u,
    phi_min_rad_m2=-5000,
    phi_max_rad_m2=5000,
    phi_step_rad_m2=1,
)
```

The same operation is available as `POST /api/rm-synthesis`. Inputs are JSON
arrays named `freqs_mhz`, `stokes_q`, and `stokes_u`; optional `sigma_q` and
`sigma_u` may be scalars or per-channel arrays.

In the browser, open **Polarization**, click **Import Q/U JSON**, and select a
file with the same request fields. FLITS plots the dirty Faraday spectrum and
RMSF and displays the peak diagnostics without attaching uncalibrated Q/U to a
Stokes-I session.

!!! warning "Dirty spectrum, not a calibrated polarization product"

    FLITS does not currently perform RM-CLEAN, ionospheric RM correction,
    instrumental leakage calibration, or polarization debiasing. These limits
    are included as machine-readable warnings. Do not report the peak as a
    publication-grade source RM until those corrections and calibration have
    been applied upstream.

FLITS requires at least eight finite channels. By default it samples the
Faraday-depth axis at five points per theoretical RMSF FWHM and uses the
wavelength sampling to choose the maximum observable absolute RM. Explicit
bounds are accepted, but grids larger than 20,001 samples are rejected to keep
interactive/API requests bounded.
