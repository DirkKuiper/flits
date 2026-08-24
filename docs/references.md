# References

The methods FLITS implements, and the software it builds on.

## Dispersion

FLITS dedisperses incoherently to the top of the band, rounding each channel's
delay to a whole sample. The dispersion constant is taken as
k = 1 / (2.41 × 10⁻⁴) MHz² pc⁻¹ cm³ s, the pulsar-astronomy convention rather
than the exact physical value; the distinction and its history are discussed by
Kulkarni.

- Manchester, R. N. & Taylor, J. H. (1977), *Pulsars*. W. H. Freeman.
- Kulkarni, S. R. (2020), *Dispersion measure: confusion, constants and clarity*.
  [arXiv:2007.02886](https://arxiv.org/abs/2007.02886)

Because shifts are rounded to samples, the achievable DM resolution is set by
the time resolution of the data. FLITS derives that limit rather than assuming
it — see `flits.signal.dedispersion_bin_resolution` and
[DM Optimization](analysis/dm-optimization.md).

## DM optimization

Two criteria are available. The integrated-event signal-to-noise metric
maximizes the S/N of the summed burst profile. The structure-maximizing metric
is a clean-room implementation of the DM\_phase algorithm, which maximizes the
coherence of the burst's Fourier phases across the band and is preferable for
bursts with sharp temporal structure.

- Seymour, A., Michilli, D. & Pleunis, Z. (2019), *DM\_phase: Algorithm for
  correcting dispersion of radio signals*. Astrophysics Source Code Library,
  [ascl:1910.004](https://ascl.net/1910.004)
- Hessels, J. W. T. et al. (2019), *FRB 121102 Bursts Show Complex Single-pulse
  Structures Consistent with Strong Propagation Effects*, ApJL 876, L23.
  [doi:10.3847/2041-8213/ab13ae](https://doi.org/10.3847/2041-8213/ab13ae)

## Calibration and fluence

Peak flux and fluence are calibrated through the radiometer equation using the
supplied system-equivalent flux density, effective bandwidth, polarization count
and sample time. Uncertainties combine the radiometer-noise statistical term
with the SEFD fractional systematic in quadrature; without the latter, the
result is reported as statistical-only and flagged as not publishable.

- Cordes, J. M. & McLaughlin, M. A. (2003), *Searches for Fast Radio Transients*,
  ApJ 596, 1142. [doi:10.1086/378231](https://doi.org/10.1086/378231)
- Lorimer, D. R. & Kramer, M. (2004), *Handbook of Pulsar Astronomy*.
  Cambridge University Press.

See [Measurements](analysis/measurements.md) for how each quantity is classified.

## Rotation-measure synthesis

The transform follows Brentjens & de Bruyn; RM-CLEAN follows Heald. FLITS
validates its output against RM-Tools reference products.

- Brentjens, M. A. & de Bruyn, A. G. (2005), *Faraday rotation measure
  synthesis*, A&A 441, 1217.
  [doi:10.1051/0004-6361:20052990](https://doi.org/10.1051/0004-6361:20052990)
- Heald, G. (2009), *The Faraday rotation measure synthesis technique*,
  Proc. IAU 4 (S259), 591.
  [doi:10.1017/S1743921309031421](https://doi.org/10.1017/S1743921309031421)
- Macquart, J.-P. et al. (2012), *The Rotation Measure and 3-Dimensional
  Magnetic Field Structure*, PASA 29, 82.
  [doi:10.1071/AS11027](https://doi.org/10.1071/AS11027)
- Purcell, C. R., Van Eck, C. L., West, J., Sun, X. H. & Gaensler, B. M. (2020),
  *RM-Tools: Rotation measure synthesis and QU-fitting*. Astrophysics Source Code
  Library, [ascl:2005.003](https://ascl.net/2005.003)

## Burst model fitting

Optional scattering and multi-component fits are delegated to `fitburst`.

- CHIME/FRB Collaboration, *fitburst: A Python package for modelling fast radio
  burst dynamic spectra*. <https://github.com/CHIMEFRB/fitburst>

## Multiscale temporal structure

Excess power as a function of timescale is measured with a Haar wavelet
decomposition against an empirical off-pulse floor.

- Haar, A. (1910), *Zur Theorie der orthogonalen Funktionensysteme*,
  Mathematische Annalen 69, 331.
  [doi:10.1007/BF01456326](https://doi.org/10.1007/BF01456326)

## Data formats

- Lorimer, D. R. (2011), *SIGPROC: Pulsar Signal Processing Programs*.
  Astrophysics Source Code Library, [ascl:1107.016](https://ascl.net/1107.016)
- Hotan, A. W., van Straten, W. & Manchester, R. N. (2004), *PSRCHIVE and
  PSRFITS: An Open Approach to Radio Pulsar Data Storage and Analysis*,
  PASA 21, 302. [doi:10.1071/AS04022](https://doi.org/10.1071/AS04022)
- CHIME/FRB Collaboration (2021), *The First CHIME/FRB Fast Radio Burst
  Catalog*, ApJS 257, 59.
  [doi:10.3847/1538-4365/ac33ab](https://doi.org/10.3847/1538-4365/ac33ab)

## Software FLITS depends on

- Aggarwal, K. et al. (2020), *Your: Your Unified Reader*, JOSS 5(52), 2750.
  [doi:10.21105/joss.02750](https://doi.org/10.21105/joss.02750) — filterbank
  and PSRFITS input.
- `jess` — statistical interference mitigation.
  <https://github.com/josephwkania/jess>
- Harris, C. R. et al. (2020), *Array programming with NumPy*, Nature 585, 357.
  [doi:10.1038/s41586-020-2649-2](https://doi.org/10.1038/s41586-020-2649-2)
- Virtanen, P. et al. (2020), *SciPy 1.0*, Nature Methods 17, 261.
  [doi:10.1038/s41592-019-0686-2](https://doi.org/10.1038/s41592-019-0686-2)
- Astropy Collaboration (2022), ApJ 935, 167.
  [doi:10.3847/1538-4357/ac7c74](https://doi.org/10.3847/1538-4357/ac7c74)
- Hunter, J. D. (2007), *Matplotlib: A 2D graphics environment*, CiSE 9, 90.
  [doi:10.1109/MCSE.2007.55](https://doi.org/10.1109/MCSE.2007.55)

## Background

- Lorimer, D. R. et al. (2007), *A Bright Millisecond Radio Burst of
  Extragalactic Origin*, Science 318, 777.
  [doi:10.1126/science.1147532](https://doi.org/10.1126/science.1147532)
- Petroff, E., Hessels, J. W. T. & Lorimer, D. R. (2022), *Fast radio bursts at
  the dawn of the 2020s*, A&ARv 30, 2.
  [doi:10.1007/s00159-022-00139-w](https://doi.org/10.1007/s00159-022-00139-w)

## Citing FLITS

See [CITATION.cff](https://github.com/DirkKuiper/flits/blob/main/CITATION.cff)
in the repository.
