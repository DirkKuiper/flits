# FLITS

FLITS, the Fast-Look Interactive Transient Suite, is browser-based software for
interactive FRB burst analysis. It brings burst inspection, masking,
measurement, DM optimization, temporal/spectral diagnostics, optional
model-based fitting, and export into one workflow-oriented tool. Input
formats include SIGPROC filterbank (`.fil`), PSRFITS search/fold data
(`.fits`, `.sf`), and CHIME/FRB HDF5 (`.h5`, `.hdf5`) including public
catalog waterfalls and beamformed `BBData` power files, extensible via a
plugin-based reader framework.

## Why FLITS exists

Turning a detected burst into a number you can defend takes several steps — tune
the DM, mask interference, place the event and off-pulse windows, choose a
spectral extent, then measure — and every one of them is a judgement call that
moves the answer.

The tools that do these steps well do them separately. Search pipelines stop at
detection; format libraries read data without offering a workflow; and the
strong single-purpose tools — `fitburst` for model fitting, DM_phase for
structure-maximizing DM, RM-Tools for rotation measure — each expect their own
input conventions. A burst campaign therefore accumulates per-instrument glue
scripts, and the decisions that produced the numbers survive only in someone's
notebook.

That gets harder for repeating sources, where the same population is observed by
many telescopes. FLITS was built for a repeater campaign spanning GBT at L and P
band, Nançay, Westerbork, Onsala, Stockert and CHIME: three file formats,
sampling times differing by more than an order of magnitude, and instruments
with different bandwidths, polarization conventions and SEFDs.

FLITS makes the session, rather than the script, the unit of analysis. One
interface covers every supported instrument, the reader layer absorbs the format
differences, telescope presets carry the instrument-specific calibration, and
[session snapshots](user-guide/exports-and-snapshots.md) make the analysis
inspectable and replayable afterwards — including headlessly, with
[`flits replay`](user-guide/headless-replay.md).

Measurements state their own uncertainty basis: FLITS separates a formal
1-sigma uncertainty from a statistical-only one, and will not mark a fluence
publishable when the SEFD systematic it needs was never supplied. See
[Measurements](analysis/measurements.md).

!!! note "Not a search pipeline"
    FLITS starts from a burst you already have. Candidate generation and
    detection are out of scope.

## What FLITS is good at

- Loading local SIGPROC, PSRFITS, or CHIME/FRB HDF5 files into a browser-based analysis session.
- Interactively defining crop windows, event windows, off-pulse regions, and
  spectral windows.
- Masking problematic channels before running measurements.
- Reporting burst measurements together with provenance and exportable state.
- Comparing DM metrics and inspecting residual arrival-time diagnostics.
- Running temporal-structure and spectral analyses on the current selection.
- Exporting structured results, plots, and session snapshots.

## Quickstart

```bash
pip install flits
flits --data-dir /path/to/filterbanks --host 127.0.0.1 --port 8123
```

Then open `http://127.0.0.1:8123`.

If you want optional selected-event model fitting as well:

```bash
pip install "fitburst @ https://github.com/CHIMEFRB/fitburst/archive/3c76da8f9e3ec7bc21951ce1b4a26a0255096b69.tar.gz"
```

## Start here

- Use [Quickstart](getting-started.md) if you want the shortest path from
  install to first session.
- Use [Guided Workflow](guided-workflow.md) for a concrete GBT burst example
  that goes from loading through preparation, measurements, DM, temporal
  diagnostics, fitting, and export.
- Use [Installation and Deployment](installation.md) if you need Docker,
  Apptainer, or remote/HPC usage.
- Use [Session Workflow](user-guide/session-workflow.md) once the interface is
  open and you want to know how to work through a burst.
- See [Supported Formats](user-guide/supported-formats.md) for the full
  reader matrix and detection rules.

## Analysis areas

- [Burst Localization](analysis/localization.md)
- [Measurements](analysis/measurements.md)
- [DM Optimization](analysis/dm-optimization.md)
- [Temporal and Spectral Analysis](analysis/temporal-spectral.md)
- [Multiscale Temporal Power](analysis/temporal-multiscale.md)
- [Sub-Burst Drift](analysis/drift.md)
- [Rotation-Measure Synthesis](analysis/rm-synthesis.md)
- [Model Fitting](analysis/model-fitting.md)

## Developer docs

- [Python API Reference](developer/api-reference.md)
- [Testing](developer/testing.md)
- [Publishing](developer/publishing.md)
- [Custom Readers](developer/custom-readers.md)

## References

Methods implemented in FLITS are attributed on the
[References](references.md) page.
