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
    orcid: 0009-0003-5650-796X
    affiliation: 1
  - name: Ziggy Pleunis
    orcid: 0000-0002-4795-697X
    affiliation: "1, 2"
  - name: Jason W. T. Hessels
    orcid: 0000-0003-2317-1446
    affiliation: "1, 2, 3, 4"
affiliations:
  - name: Anton Pannekoek Institute for Astronomy, University of Amsterdam, Science Park 904, NL-1098 XH Amsterdam, the Netherlands
    index: 1
  - name: ASTRON, Netherlands Institute for Radio Astronomy, Oude Hoogeveensedijk 4, 7991 PD Dwingeloo, the Netherlands
    index: 2
  - name: Department of Physics, McGill University, 3600 University Street, Montreal, Quebec H3A 2T8, Canada
    index: 3
  - name: Trottier Space Institute, McGill University, 3550 University Street, Montreal, Quebec H3A 2A7, Canada
    index: 4
date: 25 September 2026
bibliography: paper.bib
---

# Summary

Fast radio bursts (FRBs) are brief flashes of radio emission, usually detected
from distant galaxies [@Lorimer2007; @Petroff2022]. They are commonly studied
using a dynamic spectrum: a record of the radio signal as a function of time
and observing frequency. Measurements of burst brightness, duration, frequency
structure, and polarization help researchers investigate the source and the
material through which its radiation has travelled.

FLITS (Fast-Look Interactive Transient Suite) is an open-source Python package
with a browser interface for analysing detected bursts. It brings data
inspection, interference removal, measurement, and modelling into a single
interactive session. It also acts as a wrapper around existing tools, including
`your` for reading data, `jess` for interference mitigation, and optional
`fitburst` modelling, so users can apply them to the same selected data without
moving between separate programs.
This approach can be extended to other analysis packages by writing adapters
that use the same prepared data and session settings.

FLITS saves the measurement settings, selected data regions, excluded frequency
channels, and calibration inputs in a portable session file. Together with the
original data, this file allows users to reopen an analysis and recompute
supported measurements. Outputs describe how uncertainties were estimated and
flag missing calibration information, making published results easier to check
and reproduce.

# Statement of need

For each detected burst, radio observations provide dynamic spectra, often for
the four Stokes parameters: total intensity ($I$), linear polarization ($Q$
and $U$), and circular polarization ($V$). Some observations retain only total
intensity. Turning these data into an astrophysical interpretation requires
measurements such as dispersion measure (DM), which describes the delay of
lower-frequency radiation by free electrons, and rotation measure (RM), which
describes the wavelength-dependent rotation of linear polarization by
magnetized plasma. Other quantities include burst width, arrival time,
spectrum, fluence (flux density integrated over time), and, given calibration
and distance information, peak luminosity. Scintillation bandwidth and
scattering time characterize fine frequency structure and pulse broadening
caused by propagation through intervening material.

These derived properties are routinely reported in papers, but a table of
values rarely records every choice needed to reproduce them. Results depend
on the burst and background regions, excluded interference, time and frequency
resolution, calibration, and fitted model. Comparing observations from
instruments with different formats and sensitivities adds further complexity.
The campaign that motivated FLITS combines data from the Green Bank Telescope
at L and P band, Nançay, Westerbork, Onsala, Stockert, and CHIME.

Automated pipelines are essential for samples of hundreds to thousands of
bursts, but unusual or particularly informative events often warrant individual
attention. For example, successive components in some repeating FRBs appear
at progressively lower frequencies, the "sad-trombone" effect
[@Hessels2019]. This structure can be confused with an incorrect dispersion
correction, so the DM that maximizes signal-to-noise need not best preserve the
burst's components. Interactive inspection lets researchers compare DM choices,
separate overlapping components, select uncontaminated background data, and
check what a fitted model fails to explain. It supports careful assessment of
such ambiguities without claiming to remove them automatically.

FLITS combines this flexibility with a record of the settings used. Users can
refine an individual analysis, compare measurements across observations, and
share the saved session and original data so collaborators can examine how
the results were obtained.

# State of the field

Several packages address the analysis of bursts after detection. `fitburst`
[@fitburst] models dynamic spectra to estimate burst shape, dispersion, and
scattering parameters. FRBGui [@frbgui] provides interactive measurements of
burst structure in time and frequency. DM\_phase [@dmphase] estimates DM using
resolved burst structure, while RM-Tools [@rmtools] provides rotation-measure
synthesis and polarization fitting. Stingray [@Bachetti2024] offers broader
time-series and variability analysis, including power spectra, correlations,
and statistical modelling of astronomical light curves. These tools provide
complementary ways to study transient signals.

FLITS connects data preparation, selected specialist tools, and its own
measurements through a shared session. It uses `your` [@Aggarwal2020] to read
common radio data formats and `jess` [@Kania2026] for interference mitigation,
and passes the selected dynamic spectrum to `fitburst` for optional modelling.
Its own RM-synthesis implementation is checked against an RM-Tools reference
dataset. A separate workbench is useful because the shared preparation and
analysis record span several specialist packages and file formats. The existing
wrappers are examples of this architecture: developers can integrate further
tools through Python adapters, following the `fitburst` integration, while
reusing the session's selections and calibration. Its main contribution is to
retain those choices alongside measurements, so the interactive workflow can
be inspected and supported calculations repeated.

# Software design

The same Python analysis routines serve both the browser interface and
command-line workflows. Changes made in the browser update a session containing
the data and analysis settings; the calculations can also run without opening
a browser. This allows automated tests to exercise the measurement routines
directly.

FLITS reads SIGPROC filterbank files and PSRFITS search and folded data
[@Hotan2004], and supports CHIME/FRB HDF5 products, including public catalogue
dynamic spectra [@CHIMEFRB2021] and beamformed power data. These readers convert
the input into common arrays and descriptive information. Additional file
formats can be supported through reader plugins.

A saved JSON session file records the input-file location and SHA-256 checksum, DM,
data crop, burst and background regions, frequency range, excluded channels,
calibration inputs, and available results. This takes more care to maintain
than a table of measurements, but preserves the settings needed to revisit an
analysis. On reopening, FLITS checks the source data against the recorded
checksum and metadata. The file captures the saved state, not a history of every interaction;
reproducing the calculation also requires the original data and an appropriate
software environment.

The `flits replay` command reopens the data and recomputes the core
measurements and any recorded width comparisons, frequency-drift measurements,
and polarization analyses performed on the session data. It can compare the
core numerical results with their saved values within a specified tolerance.
The saved drift-analysis settings include the Monte Carlo random seed. DM scans, other time- and
frequency-structure analyses, and optional model fits remain saved results
rather than being rerun by this command.

Exports use JSON for structured results, CSV for tables, NumPy NPZ archives
for numeric and string arrays, PNG/SVG for plots, and optional SIGPROC files
for selected data windows. They do not serialize Python objects with pickle.
These formats allow stored results to be inspected independently of FLITS,
but do not guarantee identical recalculation after software changes. Snapshots
and exports automatically record FLITS, Python, and installed dependency
versions, with source revisions where available. Each analysis retains its
originating software record when a session is reopened or other measurements
are recomputed; missing provenance in older files remains explicitly unknown.
Tests use files generated by five historical releases to check preservation of
stored results and detect changes during recalculation. Reproducing an analysis
still requires preserving its software environment: version records identify
software but do not archive it. The repository supplies pinned dependencies,
including a specific `fitburst` revision, and a public reference workflow.

Polarization analysis accepts suitable four-polarization input files with known
conventions, or separately prepared $Q/U$ spectra. FLITS performs weighted
RM synthesis [@Brentjens2005; @Heald2009], which combines the polarization
across frequency to estimate Faraday rotation. Instrumental polarization
calibration and corrections for Earth's ionosphere must be applied separately.
Flux and fluence uncertainties distinguish measurement noise from calibration
uncertainty. When uncertainty in the system-equivalent flux density (SEFD), a
measure of telescope sensitivity, is not supplied, the outputs flag the
incomplete uncertainty budget. These checks help users judge a result's limits;
they do not establish its scientific validity on their own.

User documentation and reproducible examples are available at
<https://dirkkuiper.github.io/flits/>.

# Research impact statement

FLITS is used for FRB research within the
[AstroFlash group](https://astroflash-frb.github.io/) and the CHIME/FRB
Collaboration. It supports studies of burst energetics, structure in time and
frequency, and propagation effects by bringing intensity and polarization
measurements into a consistent workflow. Researchers can compare bursts across
observing epochs and instruments, assess how analysis choices affect the
results, and share reproducible analyses through saved sessions and the
original data.

The [public GBT reference workflow](https://dirkkuiper.github.io/flits/guided-workflow/)
provides a downloadable burst cutout, a saved session, expected measurements,
and pinned dependencies. Automated checks exercise both the published package
and the current source checkout against this reference.

# AI usage disclosure

Generative AI tools (OpenAI Codex/ChatGPT and Anthropic Claude Code) assisted
software development, documentation, and manuscript preparation, including
prose editing and review. This submission-preparation revision used Codex
(GPT-6). Earlier model versions were not systematically recorded. The
authors reviewed and validated AI-assisted outputs, made the core scientific
and design decisions, and retain responsibility for the work.

# Acknowledgements

FLITS builds on the scientific Python ecosystem, particularly NumPy
[@Harris2020], SciPy [@Virtanen2020], Astropy [@Astropy2022], and Matplotlib
[@Hunter2007], and on `your` [@Aggarwal2020] and `jess` [@Kania2026] for data
input and interference mitigation.

The AstroFlash research group at McGill University, University of Amsterdam,
ASTRON, and JIVE is supported by: a Canada Excellence Research Chair in
Transient Astrophysics (CERC-2022-00009); an Advanced Grant from the European
Research Council (ERC) under the European Union's Horizon 2020 research and
innovation programme ('EuroFlash'; Grant agreement No. 101098079); an NWO-Vici
grant ('AstroFlash'; VI.C.192.045); an NSERC Discovery Grant (RGPIN-2025-06681);
an ERC Starting Grant ('EnviroFlash'; Grant agreement No. 101223057); and an
NWO-Veni grant (VI.Veni.222.295).

The funders had no role in the software design, implementation,
validation, or preparation of this manuscript.

# References
