# Rotation-Measure Synthesis

FLITS performs weighted one-dimensional rotation-measure (RM) synthesis on a
channelized complex linear-polarization spectrum, `P = Q + iU`. It retains the
dirty Faraday dispersion function (FDF), the complex rotation-measure spread
function (RMSF), and—when requested—a separate RM-CLEAN reconstruction.

The implementation follows [Brentjens & de Bruyn
(2005)](https://doi.org/10.1051/0004-6361:20052990). RM-CLEAN follows the
one-dimensional deconvolution described by [Heald, Braun & Edmonds
(2009)](https://doi.org/10.1051/0004-6361/200811532).

## Prepare the input

RM synthesis needs a calibrated Q/U spectrum, not a Stokes-I dynamic spectrum.
Before importing data:

1. remove the Q and U off-pulse baselines;
2. apply the instrument's polarization and leakage calibration;
3. flag unusable channels consistently in Q and U;
4. integrate the selected burst window into one Q and U value per channel;
5. estimate `sigma_q` and `sigma_u` from suitable off-pulse data; and
6. retain the true channel width for the bandwidth-depolarization limit.

Frequency values are channel centres in MHz. Q, U, and their uncertainties must
use the same units. FLITS can run without uncertainties, but then uses equal
weights and estimates the noise from the residual of the best Faraday-thin
component. This is less reliable for complex sources.

## Browser workflow

Open **Polarization** in the Analysis Workspace:

1. Click **Import JSON or CSV**. The analysis runs once immediately with the
   displayed defaults.
2. Review the detected channel count, frequency range, uncertainty status, and
   channel-width status.
3. Adjust the Faraday-depth bounds or step when the automatic coverage is not
   appropriate. Blank fields use the wavelength-coverage defaults.
4. Leave **Apply RM-CLEAN** enabled when RMSF sidelobes need deconvolution. The
   dirty spectrum is always retained.
5. Click **Run RM Synthesis** after changing a setting.
6. Inspect both plots: the FDF/RMSF plot and Q/U with the best Faraday-thin
   model. A high reduced chi-square indicates that one component, the noise
   model, or the calibration does not describe the data.
7. Download the complete result as JSON and the sampled FDF as CSV.

The browser accepts either column-oriented JSON:

```json
{
  "freqs_mhz": [900.0, 901.0, 902.0],
  "stokes_q": [0.12, 0.08, -0.01],
  "stokes_u": [-0.03, 0.09, 0.13],
  "sigma_q": 0.01,
  "sigma_u": 0.01,
  "channel_width_mhz": 1.0
}
```

or a CSV table. Short aliases such as `frequency_mhz`, `q`, `u`, `q_err`, and
`u_err` are recognized:

```csv
frequency_mhz,q,u,q_err,u_err,channel_width_mhz
900.0,0.12,-0.03,0.01,0.01,1.0
901.0,0.08,0.09,0.01,0.01,1.0
902.0,-0.01,0.13,0.01,0.01,1.0
```

At least eight usable rows are required. The short examples above show the
format only and are not sufficient to run an analysis.

## Python and API usage

```python
from flits.analysis import run_rm_synthesis

result = run_rm_synthesis(
    freqs_mhz=freqs_mhz,
    stokes_q=stokes_q,
    stokes_u=stokes_u,
    sigma_q=sigma_q,
    sigma_u=sigma_u,
    channel_width_mhz=channel_width_mhz,
    phi_min_rad_m2=-5000,
    phi_max_rad_m2=5000,
    phi_step_rad_m2=1,
    clean=True,
    clean_gain=0.1,
    clean_threshold_sigma=3.0,
    clean_max_iterations=1000,
)

if result.status != "ok":
    raise ValueError(result.message)
```

The same fields are accepted by `POST /api/rm-synthesis`. Uncertainties and
channel widths may be scalars or arrays with one value per channel. Invalid
settings return a structured result status from the Python function; request
schema errors return the usual HTTP validation response.

## Automatic sampling and reported limits

By default FLITS:

- searches from `-max_abs_rm_rad_m2` to `+max_abs_rm_rad_m2`;
- samples the FDF at five points per theoretical RMSF FWHM;
- computes the maximum observable absolute RM from the widest channel in
  wavelength-squared space;
- uses the supplied channel widths, or infers local widths from channel-centre
  spacing and emits `channel_width_inferred`; and
- evaluates large transforms in bounded chunks. Grids above 100,001 points are
  rejected with guidance to narrow the range or increase the step.

The peak position is refined between grid samples with a three-point parabolic
fit to FDF power. The nominal Faraday-thin uncertainty is
`RMSF FWHM / (2 × peak S/N)`.

When uncertainties are supplied, FLITS uses inverse complex-variance weights,
propagates the Faraday noise, and reports the reduced chi-square of the best
single thin-component Q/U model. Otherwise it reports residual-MAD noise and
does not claim a chi-square. The global false-alarm probability is an
approximation based on Gaussian Q/U noise and the number of independent
RMSF-sized trials. Real calibration residuals and non-Gaussian noise can make it
too optimistic. [George, Stil & Keller
(2012)](https://doi.org/10.1071/AS11027) show why an 8-sigma threshold is a
safer default for broad RM searches than lower thresholds.

## Interpreting the output

Key fields include:

- `peak_rm_rad_m2` and `peak_rm_uncertainty_rad_m2`;
- `peak_snr` and approximate `false_alarm_probability`;
- measured and debiased peak polarized amplitudes;
- polarization angle at the weighted reference wavelength squared and the
  extrapolated zero-wavelength angle;
- RMSF FWHM and maximum sidelobe;
- largest recoverable Faraday scale and maximum observable absolute RM;
- dirty complex FDF, complex RMSF, restored RM-CLEAN FDF, and CLEAN
  components; and
- machine-readable warnings for every important caveat.

!!! warning "A Faraday-depth peak is not automatically an intrinsic source RM"

    FLITS does not calibrate instrumental leakage or position angle, and it
    does not calculate an ionospheric correction. RM-CLEAN removes RMSF
    sidelobes; it does not fix calibration errors, bandwidth depolarization, a
    poor noise model, or unresolved Faraday complexity. Apply those corrections
    upstream and preserve them in the analysis provenance before publishing a
    source RM.
