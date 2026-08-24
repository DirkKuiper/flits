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
affiliations:
  - name: Anton Pannekoek Institute for Astronomy, University of Amsterdam, Science Park 904, NL-1098 XH Amsterdam, the Netherlands
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
PSRFITS search and fold data, and CHIME/FRB HDF5 products, including public
catalogue waterfalls [@CHIMEFRB2021] and beamformed `BBData`, behind a single
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
declines to mark a fluence publishable when the required systematic input, an
SEFD fractional uncertainty, was never supplied.

# Statement of need

The practical consequence of current burst-analysis practice is that a campaign
accumulates a layer of per-instrument glue scripts. Analysis decisions such as
which channels were masked, where the off-pulse region was placed, and which DM
was adopted on what criterion survive only in a researcher's notebook, if at
all. That makes
results difficult to reproduce even for the person who produced them.

This problem sharpens for repeating sources, where the same population is
observed by many telescopes. The campaign that motivated FLITS follows a
repeater across the Green Bank Telescope at L and P band, Nançay, Westerbork,
Onsala, Stockert and CHIME, spanning three file formats, sampling times
differing by more than an order of magnitude, and instruments with different
bandwidths, polarization conventions and system-equivalent flux densities.
Measuring hundreds of bursts consistently across that heterogeneity, and being
able to demonstrate afterwards that they *were* measured consistently, is not
something a collection of per-instrument scripts supports.

FLITS addresses this by making the session, rather than the script, the unit of
analysis. One interface covers every supported instrument; the reader layer
absorbs the format differences; telescope presets carry the instrument-specific
calibration; and the snapshot makes the resulting analysis inspectable and
replayable. Researchers who need a number they can defend in referee response,
such as a fluence with a stated systematic basis, a width with a documented
noise reference, or a DM with the criterion that selected it, get it from the tool
directly rather than from reconstruction after the fact.

# State of the field

Existing software provides the required capabilities separately. Search
pipelines such as PRESTO [@presto] and Heimdall [@Barsdell2012] find candidates
but stop at detection. Format libraries such as `your` [@Aggarwal2020] and
PSRCHIVE [@Hotan2004] read and manipulate data without defining a measurement
workflow. Single-purpose tools solve one analysis step each: `fitburst`
[@fitburst] fits burst models, DM\_phase [@dmphase] optimizes DM by structure
maximization, RM-Tools [@rmtools] performs RM synthesis, and frbgui [@frbgui]
measures sub-burst drift. These packages are strong within their scope, but each
has its own inputs and none records the full sequence of interactive choices
that connects a telescope product to a published table.

FLITS therefore does not replace search pipelines, format libraries, or those
specialist methods. It reuses `your` and `jess` [@Kania2026], exposes optional
`fitburst` modelling, and validates its RM products against RM-Tools. Its
scholarly contribution is the missing integration and provenance layer: a
single state model across instruments and methods, with uncertainty semantics
and a replayable record of human decisions. Adding that layer to any one
specialist project would leave the other formats and methods fragmented and
would make the host package responsible for a workflow outside its purpose.

# Software design

FLITS separates scientific state and computation from its browser interface.
Readers normalize supported formats into a common in-memory representation;
analysis modules operate on explicit event, off-pulse, spectral, mask, and
calibration selections; and a FastAPI service presents those operations to a
local browser. Third-party readers register through Python entry points. This
design keeps the analysis testable without a browser and lets a new instrument
join the workflow without changes to the interface or the existing readers.

The central design choice is to treat a session as a versioned research object
rather than transient user-interface state. A JSON snapshot stores selections,
calibration inputs, derived products, and a content hash of the source file.
The command-line replay path restores that state, recomputes measurements, and
can compare them with the stored result. Export bundles carry measurements,
plots, and method-specific data products together. This costs more schema and
migration work than exporting only a table, but makes the analysis inspectable
and allows automated reproducibility checks.

Within that framework users can localize a burst, mask interference, measure
width, fluence and spectral extent, optimize DM, inspect temporal and spectral
structure, fit optional burst models, and perform weighted Q/U RM synthesis.
FLITS deliberately reads total-intensity products; polarization analysis begins
from an imported calibrated Q/U spectrum. Explicit scope boundaries avoid
implying calibration capabilities that the readers do not provide.

# Research impact

FLITS is used by the author in the ongoing multi-telescope repeater campaign
that motivated its design. The same saved-session and replay path is applied to
bursts from GBT, Nançay, Westerbork, Onsala, Stockert, and CHIME so that a
measurement can be traced to the same selection and calibration schema despite
different native products. This is realized research use rather than a
prospective example.

For independent evaluation, the public guided workflow applies the released
software to a downloadable GBT burst and gives expected intermediate and final
values. The repository also includes synthetic tests, a reference RM-Tools data
set, and end-to-end browser tests. FLITS is distributed through PyPI and a
container image, documented at <https://dirkkuiper.github.io/flits/>, and
supports external reader plug-ins. Together these materials let reviewers and
future adopters reproduce a complete analysis, inspect numerical agreement,
and extend the software without access to the campaign's private data.

# AI usage disclosure

Generative-AI coding assistants were used during software development and
publication preparation for code and documentation review, refactoring
suggestions, test scaffolding, and prose drafting. The author selected and
reviewed all changes. AI-assisted code was subjected to the same automated
tests, static analysis, browser tests, packaging checks, and container checks as
other contributions; scientific claims and bibliographic metadata were checked
against the implementation, reference outputs, and primary sources. The author
retains responsibility for the software and this manuscript.

# Acknowledgements

FLITS builds on the scientific Python ecosystem, in particular NumPy
[@Harris2020], SciPy [@Virtanen2020], Astropy [@Astropy2022] and Matplotlib
[@Hunter2007], and on the `your` [@Aggarwal2020] and `jess` [@Kania2026] libraries for
filterbank input and interference mitigation.

DK acknowledges funding from the European Research Council under the European
Union's Horizon 2020 research and innovation programme (ERC Advanced Grant
"EuroFlash", grant agreement No. 101098079). The AstroFlash research group is
additionally supported by a Canada Excellence Research Chair in Transient
Astrophysics (CERC-2022-00009) and an NWO Vici grant ("AstroFlash",
VI.C.192.045). The funders had no role in the software design, implementation,
validation, or preparation of this manuscript.

# References
