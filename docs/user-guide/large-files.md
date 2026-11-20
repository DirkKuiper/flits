# Large Files

FLITS loads a **read window** of a file into memory, dedisperses it and
normalizes each channel. By default the window is the whole file. That suits
the short files most bursts are cut into, but an hour of search-mode data can
be far larger than memory. FLITS starts from a burst you already have, so it
only needs the part of the file that holds that burst.

## Check the size first

```bash
flits inspect search.fil
```

Besides the header metadata, each record reports:

| Field | Meaning |
| --- | --- |
| `duration_s`, `nsamples` | Length of the file. |
| `bytes_on_disk` | Size of the file. |
| `estimated_load_bytes` | Peak memory FLITS expects loading the **whole** file as Stokes I to take. |
| `load_budget_bytes` | The current memory budget for one load; `null` means no limit. |
| `max_window_s` | The longest read window that fits the budget; `null` when there is no limit, or when the reader reads the whole file regardless. |

`metadata` adds the layout behind the estimate: `nchans`, `tsamp_s`, `nbits`,
`data_bytes`, and `windowed_read` (whether the reader reads only the window
from disk).

## Load only the burst

Give the burst's arrival time and FLITS loads just the span that holds it:

```bash
flits measure search.fil --dm 527.7 --around 312.418
```

`--around T` is in **seconds from the start of the file**, for the arrival at
the **top of the band** (the highest channel frequency), which is the reference
FLITS dedisperses to. FLITS reads

```text
[T - pad, T + sweep + pad]
```

where the sweep is the dispersion delay across the band at `--dm`, so the low
frequencies of the burst are inside the window, and `--pad` (default 0.5 s) is
kept on either side for the off-pulse noise. For CHIME BBData, which is
coherently dedispersed on disk, the sweep is that of the residual DM.

!!! note "Times referenced to infinite frequency"
    Search pipelines sometimes report arrival times at infinite frequency.
    Those are earlier than the top-of-band arrival by the dispersion delay at
    the top frequency. Convert the time, or increase `--pad` to cover the
    difference.

To load a span you choose, give it in seconds from the start of the file:

```bash
flits measure search.fil --dm 527.7 --read 311.5 314.0
```

`--around` and `--read` exclude each other. In a manifest the same settings are
`burst_time_s` (with `burst_pad_s`) and `read_window_s`; see
[Batch Manifests and Schemas](automation-contracts.md).

**Times in the results stay in milliseconds from the start of the file**,
whatever was loaded: a burst at 312.418 s has its event window near
312 418 ms with or without `--around`. Each record's
`selection.read_window_s` says which span was loaded.

The automatic time downsampling depends on the window's length, so a short
window is analysed at a finer resolution than the whole file. Pin
`--time-factor` when you compare measurements made with different windows.

From Python:

```python
import flits

session = flits.BurstSession.from_file("search.fil", dm=527.7, burst_time_sec=312.418)
record = flits.measure_burst("search.fil", flits.MeasureOptions(dm=527.7, burst_time_s=312.418))
```

`BurstSession.from_file` also accepts `burst_pad_sec`, or `read_start_sec` and
`read_end_sec` for an explicit window. The web API's `POST /api/sessions`
accepts `burst_time_sec` and `burst_pad_sec` too.

## The memory budget

Before reading a single sample, FLITS estimates the peak memory of the load
from the header and refuses a load that exceeds the budget. Without that
check, a file several times larger than memory would exhaust it, and a server
would stop for every session it holds. The refusal says how large the load
would be and how long a window would fit. For ten minutes of 8-bit data with
1024 channels and 64 µs samples, on a machine with 16 GB available:

```text
LoadTooLargeError: Loading the read window 0.000-600.000 s of 600.000 s of search.fil would
need about 57.6 GB of memory (1024 channels x 9375000 samples); the load budget is 8.0 GB.
Load only the burst: give its arrival time with --around T (Python: burst_time_sec=T), or a
window of at most 83.333 s with --read START END (Python: read_start_sec/read_end_sec).
To allow larger loads, pass --max-load-gb GB (Python: max_load_gb) or set FLITS_MAX_LOAD_GB.
```

`flits measure` reports it as an `error` record for that burst and carries on
with the rest; the web interface shows it as a 413 response with the same
message.

The budget is, in order of precedence:

1. `--max-load-gb GB` on `flits measure`, or `max_load_gb=` in Python
   (`BurstSession.from_file`, `measure_burst`);
2. the `FLITS_MAX_LOAD_GB` environment variable, which also sets it for
   `flits serve`;
3. half the memory available when the load starts, limited by a container's
   memory limit when FLITS runs in one.

A value of `0` turns the check off. GB here means 10⁹ bytes.

### What the estimate counts

The estimate is the raw samples of the window as the reader holds them, plus
the float32 working copies alive at the load's peak: about one for Stokes I
(two when Stokes I is the sum of two polarizations) and about nine per Stokes
parameter for a full I/Q/U/V cube, which is checked separately when the
polarization analysis loads it. It is measured against the actual peak in the
test suite and errs on the high side by at most 40%. A Stokes I load
therefore peaks at about twice the window's size on disk for 32-bit data and
about five times for 8-bit data.

Two readers read the whole file before cutting the window: folded PSRFITS and
CHIME BBData. For those a smaller window does not reduce the load, and the
refusal says so. SIGPROC filterbanks, PSRFITS search-mode files and the other
CHIME layouts read only the window from disk.

The analyses that follow the load (masking, localization, fitting) work on the
loaded window and add some memory of their own; the budget leaves room for
them by default.
