---
title: "FLITS: an interactive, reproducible workbench for fast radio burst analysis"
tags:
  - Python
  - astronomy
  - radio astronomy
  - fast radio bursts
  - transients
authors:
  - name: Dirk Kuiper
    orcid: 0000-0000-0000-0000
    affiliation: 1
affiliations:
  - name: Anton Pannekoek Institute for Astronomy, University of Amsterdam, The Netherlands
    index: 1
date: 24 August 2026
bibliography: paper.bib
---

# Summary

Fast radio bursts (FRBs) are millisecond-duration radio transients of
extragalactic origin [@Lorimer2007; @Petroff2022]. Turning a single detected
burst into publishable measurements is an interactive process: the dispersion
measure has to be tuned, radio-frequency interference masked, the burst and an
off-pulse reference region delimited in time and frequency, and only then can
width, fluence, spectral extent and polarization properties be measured. Each of
those steps involves judgement calls that determine the numbers that end up in a
paper.

FLITS (Fast-Look Interactive Transient Suite) is browser-based software that
brings this whole process into one session. It reads SIGPROC filterbank,
PSRFITS search and fold data, and CHIME/FRB HDF5 products — including public
catalogue waterfalls [@CHIMEFRB2021] and beamformed `BBData` — behind a single
reader interface that third parties can extend through entry points without
forking. Within a session a user tunes the DM, masks channels, localizes the
burst automatically or by hand, and obtains calibrated measurements, DM
optimization by integrated signal-to-noise or by structure maximization,
temporal and spectral structure diagnostics, rotation-measure synthesis
[@Brentjens2005; @Heald2009], and optional scattering fits.

The distinguishing feature is that every one of those judgement calls is
captured. A session snapshot is a JSON document recording the source file and
its content hash, the DM, the crop, event and off-pulse windows, the channel
mask, the spectral extent, and the calibration inputs. It can be reopened, sent
to a collaborator, archived alongside a paper, or replayed without a browser
through `flits replay`, which reproduces the exported measurements from the
snapshot alone. Measurements carry an explicit uncertainty classification: FLITS
distinguishes a formal 1-sigma uncertainty from a statistical-only one and
declines to mark a fluence publishable when the required systematic input — an
SEFD fractional uncertainty — was never supplied.

# Statement of need

Analysing a burst well requires several distinct capabilities, and the existing
software landscape provides them separately. Search pipelines such as PRESTO
[@presto] and Heimdall find candidates but stop at detection. Format libraries
such as `your` [@Aggarwal2020] and PSRCHIVE [@Hotan2004] read and manipulate
data without offering a measurement workflow. Single-purpose tools solve one
step each: `fitburst` [@fitburst] fits burst models, DM\_phase [@dmphase]
optimizes DM by structure maximization, RM-Tools [@rmtools] performs RM
synthesis, and frbgui [@frbgui] measures sub-burst drift. Each expects its own
input conventions.

The practical consequence is that a burst campaign accumulates a layer of
per-instrument glue scripts, and the analysis decisions — which channels were
masked, where the off-pulse region was placed, which DM was adopted and on what
criterion — survive only in a researcher's notebook, if at all. That makes
results difficult to reproduce even for the person who produced them.

This problem sharpens for repeating sources, where the same population is
observed by many telescopes. The campaign that motivated FLITS follows a
repeater across the Green Bank Telescope at L and P band, Nançay, Westerbork,
Onsala, Stockert and CHIME, spanning three file formats, sampling times
differing by more than an order of magnitude, and instruments with different
bandwidths, polarization conventions and system-equivalent flux densities.
Measuring hundreds of bursts consistently across that heterogeneity — and being
able to demonstrate afterwards that they *were* measured consistently — is not
something a collection of per-instrument scripts supports.

FLITS addresses this by making the session, rather than the script, the unit of
analysis. One interface covers every supported instrument; the reader layer
absorbs the format differences; telescope presets carry the instrument-specific
calibration; and the snapshot makes the resulting analysis inspectable and
replayable. Researchers who need a number they can defend in referee response —
a fluence with a stated systematic basis, a width with a documented noise
reference, a DM with the criterion that selected it — get it from the tool
directly rather than from reconstruction after the fact.

# Functionality

- **Input.** SIGPROC filterbank (`.fil`), PSRFITS search and fold mode
  (`.fits`, `.sf`, `.ar`), and CHIME/FRB HDF5, with automatic format detection
  and telescope preset matching. Additional readers register through
  `importlib` entry points.
- **Preparation.** Interactive crop, event window, off-pulse region and
  spectral extent selection; manual and automatic channel masking via `jess`;
  one-click burst localization in time and frequency using a matched-filter
  search calibrated against red noise.
- **Measurement.** Width by several methods, peak flux and fluence calibrated
  through the radiometer equation when an SEFD is available, isotropic energy
  when a distance is supplied, and per-quantity uncertainty provenance.
- **Dispersion.** DM sweeps scored by integrated event signal-to-noise or by a
  clean-room implementation of the DM\_phase structure-maximization criterion,
  with the achievable DM resolution derived from the time resolution rather
  than assumed.
- **Structure.** Temporal-structure and power-spectral diagnostics, Haar
  multiscale excess power, autocorrelation analysis, and optional scattering
  and multi-component fits through `fitburst`.
- **Polarization.** Weighted Q/U rotation-measure synthesis with optional
  RM-CLEAN, significance and quality diagnostics, and JSON/CSV products
  validated against RM-Tools reference output.
- **Output.** Export bundles containing measurements, plots and data products,
  and JSON session snapshots that `flits replay` can re-run headlessly.

FLITS is distributed on PyPI and as a container image, is tested on synthetic
data across a supported dependency range, and is documented at
<https://dirkkuiper.github.io/flits/>.

# Acknowledgements

FLITS builds on the scientific Python ecosystem, in particular NumPy
[@Harris2020], SciPy [@Virtanen2020], Astropy [@Astropy2022] and Matplotlib
[@Hunter2007], and on the `your` [@Aggarwal2020] and `jess` libraries for
filterbank input and interference mitigation.

# References
