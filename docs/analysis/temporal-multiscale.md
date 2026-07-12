# Noise-subtracted multiscale temporal power

`haar_excess_power` measures how a burst profile's temporal power is
distributed across scales. At every scale it forms Haar differences between
the means of adjacent windows at all possible offsets. The same calculation is
made in each contiguous off-pulse run, and the pooled off-pulse power is
subtracted from the event power.

This is intended for population studies where a threshold statistic such as
the smallest significant scale would move systematically with S/N. The result
contains the event, noise, signed excess, and positive normalized excess power
at every retained scale, plus two summaries:

- `characteristic_scale_ms`: the positive-excess-power-weighted geometric
  mean scale;
- `dominant_scale_ms`: the scale with the largest positive excess power.

Use `excess_power_fraction_below` to integrate the normalized power below a
common physical scale. Choose that scale for the population, not separately
for each burst, and only compare instruments over their shared resolvable
scale range.

```python
from flits.analysis import haar_excess_power

result = haar_excess_power(
    event_profile,
    [offpulse_before, offpulse_after],
    tsamp_ms=0.016,
)
```

The estimator is amplitude-weighted and empirically noise-subtracted, but it
is not automatically free of selection effects. Event-window selection,
finite resolution, scattering, band occupancy, and low S/N can still alter the
power distribution. Population claims should therefore be calibrated with
end-to-end injections and with bright bursts degraded to a common S/N.
