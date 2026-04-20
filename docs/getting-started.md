# Quickstart

This page is the shortest path from install to a working FLITS session.

## 1. Install FLITS

```bash
pip install flits
```

Optional scattering fits are powered by `fitburst` and must currently be
installed separately:

```bash
pip install "fitburst @ https://github.com/CHIMEFRB/fitburst/archive/3c76da8f9e3ec7bc21951ce1b4a26a0255096b69.tar.gz"
```

## 2. Start the local server

Point FLITS at a directory containing your filterbanks:

```bash
flits --data-dir /path/to/filterbanks --host 127.0.0.1 --port 8123
```

Open `http://127.0.0.1:8123`.

## 3. Load a burst file

FLITS reads SIGPROC filterbank (`.fil`), search-mode PSRFITS (`.fits`,
`.sf`), and CHIME/FRB HDF5 (`.h5`, `.hdf5`) files, including CHIME public
catalog waterfalls and beamformed `BBData` power files. See
[Supported Formats](user-guide/supported-formats.md) for the full matrix.

Inside the UI:

1. Pick a file from the directory list or type a path manually.
2. Enter the burst DM.
   For CHIME catalog waterfalls FLITS will suggest `0`, and for CHIME
   beamformed `BBData` files it will suggest the file's `DM_coherent`.
3. Adjust telescope/preset settings if the detected preset is not what you want.
4. Load the session.

If FLITS can identify the observing setup, it may populate default calibration
values such as SEFD. If it cannot, use the generic preset or set an explicit
SEFD yourself.

## 4. Work through the session

The typical order is:

1. Prepare the crop, event window, off-pulse window, spectral window, and mask.
2. Compute measurements.
3. Run DM optimization if needed.
4. Run temporal or fitting tools on the current selection.
5. Build an export bundle or save a session snapshot.

For the interface walkthrough, continue to
[Session Workflow](user-guide/session-workflow.md).
