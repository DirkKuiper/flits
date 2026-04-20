# FLITS

FLITS, the Fast-Look Interactive Transient Suite, is browser-based software for
interactive FRB burst analysis. It brings burst inspection, masking,
measurement, DM optimization, temporal/spectral diagnostics, optional
model-based fitting, and export into one workflow-oriented tool. Input
formats include SIGPROC filterbank (`.fil`), search-mode PSRFITS
(`.fits`, `.sf`), and CHIME/FRB HDF5 (`.h5`, `.hdf5`) including public
catalog waterfalls and beamformed `BBData` power files, extensible via a
plugin-based reader framework.

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

If you want the optional fitburst-backed scattering workflow as well:

```bash
pip install "fitburst @ https://github.com/CHIMEFRB/fitburst/archive/3c76da8f9e3ec7bc21951ce1b4a26a0255096b69.tar.gz"
```

## Start here

- Use [Quickstart](getting-started.md) if you want the shortest path from
  install to first session.
- Use [Installation and Deployment](installation.md) if you need Docker,
  Apptainer, or remote/HPC usage.
- Use [Session Workflow](user-guide/session-workflow.md) once the interface is
  open and you want to know how to work through a burst.
- See [Supported Formats](user-guide/supported-formats.md) for the full
  reader matrix and detection rules.

## Analysis areas

- [Measurements](analysis/measurements.md)
- [DM Optimization](analysis/dm-optimization.md)
- [Temporal and Spectral Analysis](analysis/temporal-spectral.md)
- [Scattering Fits](analysis/scattering-fits.md)

## Developer docs

- [Testing](developer/testing.md)
- [Publishing](developer/publishing.md)
