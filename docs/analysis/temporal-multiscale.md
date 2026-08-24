# Noise-subtracted multiscale temporal power

`haar_excess_power` measures how a burst profile's temporal power is distributed
across timescales, with the noise contribution removed empirically rather than
assumed.

It exists because the obvious statistic — "the smallest significant timescale in
this burst" — is not comparable between bursts. That quantity is a detection
threshold, so it moves with signal-to-noise: the same burst observed twice as
brightly appears to contain finer structure. Any population trend measured that
way is partly a trend in S/N.

## What it computes

At each scale \( s \) (a window width in samples), the estimator forms Haar
differences between the means of adjacent windows, at every offset in the event
window:

$$
d_s(i) = \frac{1}{s}\sum_{j=i}^{i+s-1} x_j \;-\; \frac{1}{s}\sum_{j=i+s}^{i+2s-1} x_j
$$

The power at that scale is \( \langle d_s^2 \rangle \) over all offsets. A Haar
difference responds to change on its own scale and cancels anything constant
across the pair, so this decomposes the profile by how fast it varies.

The same calculation runs over each contiguous off-pulse run, and the pooled
off-pulse power at each scale is subtracted from the event power:

$$
P_\mathrm{excess}(s) = P_\mathrm{event}(s) - P_\mathrm{off}(s)
$$

Noise contributes power at every scale, so subtracting a measured off-pulse
spectrum rather than a white-noise model removes both the radiometer term and
whatever correlated noise the off-pulse region contains.

## What comes back

`HaarExcessPowerResult` holds the event power, the noise power, the signed
excess and the positive normalized excess at every retained scale, plus two
summaries:

| Field | Meaning |
| --- | --- |
| `characteristic_scale_ms` | Geometric mean scale, weighted by positive excess power. A single number describing where the burst's variability sits. |
| `dominant_scale_ms` | The single scale carrying the most positive excess power. |

`excess_power_fraction_below` integrates the normalized excess power at scales
no larger than a given one — "what fraction of this burst's variability is
faster than 1 ms".

```python
from flits.analysis import excess_power_fraction_below, haar_excess_power

result = haar_excess_power(
    event_profile,
    [offpulse_before, offpulse_after],
    tsamp_ms=0.016,
)

print(result.characteristic_scale_ms, result.dominant_scale_ms)
print(excess_power_fraction_below(result, 1.0))
```

## Using it across a population

The estimator is amplitude-weighted and noise-subtracted, which removes the
first-order S/N dependence. It does not remove selection effects, and several
remain:

- **Event-window selection.** The window sets the largest measurable scale.
  Choose it consistently.
- **Finite resolution.** No instrument reports power below its own sample time.
  When comparing instruments, restrict to the range of scales *all* of them
  resolve — otherwise the finer-sampled instrument appears to contain more fast
  structure by construction.
- **Scattering.** Scattering suppresses fine structure genuinely, but it is a
  propagation effect, not an emission property.
- **Band occupancy** and **low S/N** both alter the recovered distribution.

Two rules follow. Choose the comparison scale for the population, not per
burst — otherwise you are comparing different questions. And calibrate with
end-to-end injections plus bright bursts degraded to a common S/N, so the size
of the residual bias is measured rather than hoped away.

## Relationship to the other temporal diagnostics

Use this rather than the minimum-structure scan in
[Temporal and Spectral Analysis](temporal-spectral.md) whenever the comparison
is between bursts of unequal brightness. The scan answers "what is the finest
structure I can demonstrate in *this* burst", which is the right question for a
single burst and the wrong one for a population.
