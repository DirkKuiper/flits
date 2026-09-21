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
  - name: Trottier Space Institute at McGill, McGill University, 3550 University Street, Montreal, Quebec H3A 2A7, Canada
    index: 4
date: 21 September 2026
bibliography: paper.bib
---

# Summary

Fast radio bursts (FRBs) are brief flashes of radio emission, usually detected
from distant galaxies [@Lorimer2007; @Petroff2022]. Their duration, brightness,
frequency structure, and polarization help researchers investigate their
sources and the material through which the signals have travelled. Measuring
these properties requires choices about interference removal, background
estimation, and which parts of a burst to include. Retaining those choices is
essential for interpreting and reproducing the measurements.

FLITS (Fast-Look Interactive Transient Suite) is an open-source Python package
with a browser interface for analysing individual detected bursts. It brings
data inspection, interference masking, burst selection, measurement, and
export into a shared workflow across several radio-telescope data formats.
Its analysis tools include corrections for frequency-dependent arrival times,
measurements of burst duration and fluence, time-frequency structure
diagnostics, polarization analysis, and optional burst modelling.

FLITS records the selected analysis state in a portable session snapshot,
including the input-file identity, data selections, channel masks, and
calibration inputs. Given the snapshot and original data, users can reopen an
analysis or recompute supported measurements without a browser. Outputs also
identify their uncertainty basis and flag missing calibration information.
This combination supports consistent measurements across instruments while
preserving the choices needed to inspect and reproduce the analysis.

# Statement of need

FRB researchers often combine observations from telescopes with different data
formats, time resolutions, bandwidths, and calibration conventions. Comparing
burst properties across these observations requires consistent definitions of
the burst window, background region, usable frequency range, and adopted
dispersion measure (DM). DM describes the frequency-dependent propagation
delay and can affect both the measured duration and apparent burst structure.

The campaign that motivated FLITS combines observations from the Green Bank
Telescope at L and P band, Nançay, Westerbork, Onsala, Stockert, and CHIME.
Maintaining separate analysis scripts for these instruments introduces repeated
format-conversion work and makes it harder to preserve a consistent record of
measurement settings. A table of final values alone cannot establish which
channels, noise samples, or calibration assumptions produced each result.

FLITS serves researchers who need to inspect bursts interactively while
retaining an explicit, reusable analysis state. Instrument readers provide a
common data representation, telescope presets supply configurable calibration
defaults, and saved sessions retain the selections associated with each
measurement. This supports comparisons across a campaign and lets
collaborators revisit an analysis without reconstructing its settings from
separate scripts and notes.

# State of the field

FLITS builds on an established ecosystem of radio-astronomy software. PRESTO
[@presto] provides pulsar search and analysis tools, while Heimdall supports
single-pulse searches using accelerated dedispersion [@Barsdell2012]. The `your`
library [@Aggarwal2020] provides unified access to common time-domain formats,
and PSRCHIVE [@Hotan2004] supports pulsar data processing, calibration, and
analysis. More specialized packages include `fitburst` for modelling dynamic
spectra [@fitburst], DM\_phase for structure-based DM estimation [@dmphase],
RM-Tools for Faraday-rotation analysis [@rmtools], and FRBGui for interactive
measurements of burst spectro-temporal properties [@frbgui].

FLITS focuses on coordinating these kinds of measurements through one
instrument-independent session model. Its contribution is the connection
between interactive selections, calibration and uncertainty metadata, and
replayable measurement outputs. Implementing this workflow as a separate
package allows its session representation to span several formats and analysis
methods without tying it to one specialist application's data model. FLITS
reuses `your` and `jess` [@Kania2026], integrates optional `fitburst` modelling,
and checks its RM-synthesis outputs against an RM-Tools reference dataset.
This design combines existing capabilities with explicit provenance for the
measurement workflow.

# Software design

FLITS separates the scientific session from its browser interface. Readers
normalize SIGPROC filterbank, PSRFITS search and folded data, and supported
CHIME/FRB HDF5 products into a common representation. CHIME inputs include
public catalogue waterfalls [@CHIMEFRB2021] and beamformed power products.
Additional readers can register through Python entry points. Analysis modules
operate on the session's selections, while a FastAPI service exposes them to a
browser. Keeping computation independent of the interface supports automated
testing and command-line use.

The session is the central unit of analysis. Its versioned JSON snapshot stores
the source-file reference and SHA-256 hash, DM, crop, burst and off-pulse
windows, frequency selection, channel mask, calibration inputs, and available
analysis results. Recording this state requires more serialization and
compatibility handling than exporting a measurement table, but makes the
settings inspectable and reusable. A snapshot records the saved state; it is
not a chronological log of every interaction or a substitute for the input
data and software environment.

The `flits replay` command reopens the source data and recomputes the core
measurements and recorded width, drift, and in-session polarization analyses.
It can compare core numerical measurement outputs with stored values. DM
sweeps, temporal and spectral diagnostics, and optional model fits are retained
as stored results rather than rerun by this command. Export bundles collect
measurements, plots, and method-specific products for further analysis.

Polarization analysis supports both suitable four-product input files with an
established polarization basis and separately prepared Q/U spectra. FLITS
performs weighted rotation-measure synthesis [@Brentjens2005; @Heald2009];
instrumental polarization calibration and ionospheric corrections remain
upstream responsibilities. For intensity measurements, uncertainty metadata
distinguishes statistical errors from estimates that include supplied
calibration uncertainties. Flux and fluence estimates without an SEFD
(system-equivalent flux density) uncertainty are explicitly flagged as having
incomplete uncertainty budgets. These labels expose assumptions for scientific
assessment rather than guaranteeing that a measurement is suitable for publication.

# Research impact statement

FLITS is used in the first author's ongoing multi-telescope repeater project
and in routine analysis within the AstroFlash research group. It was also used
in a summer research project to determine burst dispersion measures. These
applications use the software for current scientific analysis and provide
practical use cases for its shared measurement workflow.

Public development records document additional non-author engagement. A user
reported a need to analyse folded pulsar data for scattering measurements in
[issue 62](https://github.com/DirkKuiper/flits/issues/62), and contributed an
initial implementation through
[pull request 65](https://github.com/DirkKuiper/flits/pull/65). These records
provide a concrete example of a research use case extending format support.

For independent evaluation, the
[guided workflow](https://dirkkuiper.github.io/flits/guided-workflow/)
provides a downloadable GBT burst, analysis instructions, and reference values.
A separate executable example compares RM-synthesis outputs with a pinned
RM-Tools dataset. Synthetic tests and browser tests exercise the analysis and
interface. FLITS is distributed through PyPI and container images, with user
and developer documentation at <https://dirkkuiper.github.io/flits/>.

# AI usage disclosure

OpenAI Codex and ChatGPT, and Anthropic Claude Code, were used during
software development, documentation, and manuscript preparation. Assistance
included code generation and review, refactoring, test scaffolding, and prose
drafting and editing. Tools and models changed during development; historical
model names and versions were not systematically recorded.

The first author reviewed, edited, and validated AI-assisted contributions and
made the core scientific and software-design decisions with the co-authors.
Validation included automated tests, static analysis, browser tests, packaging
and container checks, and comparison of scientific claims and references with
the implementation and primary sources. The authors retain responsibility for
the software and manuscript.

# Acknowledgements

FLITS builds on the scientific Python ecosystem, particularly NumPy
[@Harris2020], SciPy [@Virtanen2020], Astropy [@Astropy2022], and Matplotlib
[@Hunter2007], and on `your` [@Aggarwal2020] and `jess` [@Kania2026] for data
input and interference mitigation.

DK acknowledges funding from the European Research Council under the European
Union's Horizon Europe research and innovation programme (ERC Advanced Grant
"EuroFlash", grant agreement No. 101098079). The AstroFlash research group is
additionally supported by a Canada Excellence Research Chair in Transient
Astrophysics (CERC-2022-00009) and an NWO Vici grant ("AstroFlash",
VI.C.192.045). The funders had no role in the software design, implementation,
validation, or preparation of this manuscript.

# References
