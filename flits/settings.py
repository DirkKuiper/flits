from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AutoMaskProfile:
    key: str
    label: str
    memory_budget_mb: int
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "memory_budget_mb": self.memory_budget_mb,
            "description": self.description,
        }


@dataclass(frozen=True)
class TelescopePreset:
    key: str
    label: str
    telescope_ids: tuple[int, ...] = ()
    machine_ids: tuple[int, ...] = ()
    sefd_jy: float | None = None
    read_start_sec: float = 0.0
    read_end_sec: float | None = None
    normalization_tail_fraction: float = 0.25
    telescope_name_aliases: tuple[str, ...] = ()
    format_signatures: tuple[str, ...] = ()
    freq_bands_mhz: tuple[tuple[float, float], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "telescope_ids": list(self.telescope_ids),
            "machine_ids": list(self.machine_ids),
            "sefd_jy": self.sefd_jy,
            "read_start_sec": self.read_start_sec,
            "read_end_sec": self.read_end_sec,
            "normalization_tail_fraction": self.normalization_tail_fraction,
            "telescope_name_aliases": list(self.telescope_name_aliases),
            "format_signatures": list(self.format_signatures),
            "freq_bands_mhz": [list(band) for band in self.freq_bands_mhz],
        }


@dataclass(frozen=True)
class ReceiverBandCalibration:
    label: str
    freq_lo_mhz: float
    freq_hi_mhz: float
    sefd_jy: float

    def contains(self, freq_mhz: float) -> bool:
        return self.freq_lo_mhz <= freq_mhz <= self.freq_hi_mhz

    def overlap_mhz(self, observed_lo_mhz: float, observed_hi_mhz: float) -> float:
        return max(0.0, min(self.freq_hi_mhz, observed_hi_mhz) - max(self.freq_lo_mhz, observed_lo_mhz))


PRESETS: dict[str, TelescopePreset] = {
    "generic": TelescopePreset(
        key="generic",
        label="Generic Filterbank",
        telescope_ids=(),
        machine_ids=(),
        sefd_jy=None,
        read_start_sec=0.0,
        read_end_sec=None,
        normalization_tail_fraction=0.25,
    ),
    "nrt": TelescopePreset(
        key="nrt",
        label="NRT",
        telescope_ids=(3,),
        machine_ids=(),
        sefd_jy=35.0 / 1.4,
        read_start_sec=0.05,
        read_end_sec=0.201,
        normalization_tail_fraction=0.25,
    ),
    "gbt": TelescopePreset(
        key="gbt",
        label="GBT",
        telescope_ids=(6,),
        machine_ids=(),
        sefd_jy=None,
        read_start_sec=0.0,
        read_end_sec=None,
        normalization_tail_fraction=0.25,
    ),
    "lofar": TelescopePreset(
        key="lofar",
        label="LOFAR",
        telescope_ids=(11,),
        machine_ids=(11,),
        sefd_jy=None,
        read_start_sec=0.0,
        read_end_sec=None,
        normalization_tail_fraction=0.25,
    ),
    "chime": TelescopePreset(
        key="chime",
        label="CHIME/FRB",
        telescope_ids=(),
        machine_ids=(),
        sefd_jy=None,
        read_start_sec=0.0,
        read_end_sec=None,
        normalization_tail_fraction=0.25,
        telescope_name_aliases=("chime", "chimefrb"),
        format_signatures=(
            "chime_frb_catalog_v1",
            "flits_chime_v1",
            "chime_bbdata_beamformed_v1",
        ),
        freq_bands_mhz=((400.0, 800.0),),
    ),
}

AUTO_MASK_PROFILES: dict[str, AutoMaskProfile] = {
    "fast": AutoMaskProfile(
        key="fast",
        label="Fast",
        memory_budget_mb=32,
        description="Lower memory use and quicker masking on large files.",
    ),
    "auto": AutoMaskProfile(
        key="auto",
        label="Auto",
        memory_budget_mb=96,
        description="Balanced default for typical interactive masking.",
    ),
    "thorough": AutoMaskProfile(
        key="thorough",
        label="Thorough",
        memory_budget_mb=192,
        description="Use more off-burst data when memory headroom allows.",
    ),
}

GBT_BAND_CALIBRATIONS: tuple[ReceiverBandCalibration, ...] = (
    ReceiverBandCalibration(label="L-band", freq_lo_mhz=1150.0, freq_hi_mhz=1730.0, sefd_jy=10.0),
    ReceiverBandCalibration(label="S-band", freq_lo_mhz=1730.0, freq_hi_mhz=2600.0, sefd_jy=11.0),
    ReceiverBandCalibration(label="C-band", freq_lo_mhz=3950.0, freq_hi_mhz=7800.0, sefd_jy=9.0),
    ReceiverBandCalibration(label="X-band", freq_lo_mhz=7800.0, freq_hi_mhz=12000.0, sefd_jy=15.0),
    ReceiverBandCalibration(label="Ku-band", freq_lo_mhz=12000.0, freq_hi_mhz=15400.0, sefd_jy=15.8),
)


def available_presets() -> list[TelescopePreset]:
    return list(PRESETS.values())


def available_auto_mask_profiles() -> list[AutoMaskProfile]:
    return list(AUTO_MASK_PROFILES.values())


def get_preset(preset_key: str | None = None) -> TelescopePreset:
    key = "generic" if preset_key is None else str(preset_key).lower()
    preset = PRESETS.get(key)
    if preset is None:
        valid = ", ".join(sorted(PRESETS))
        raise ValueError(f"Unknown preset '{preset_key}'. Valid presets: {valid}.")
    return preset


def get_auto_mask_profile(profile_key: str | None = None) -> AutoMaskProfile:
    key = "auto" if profile_key is None else str(profile_key).lower()
    profile = AUTO_MASK_PROFILES.get(key)
    if profile is None:
        valid = ", ".join(sorted(AUTO_MASK_PROFILES))
        raise ValueError(f"Unknown auto-mask profile '{profile_key}'. Valid profiles: {valid}.")
    return profile


def _normalize_name(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def detect_preset(
    telescope_id: int | None,
    machine_id: int | None,
    *,
    telescope_name: str | None = None,
    schema_version: str | None = None,
    freq_lo_mhz: float | None = None,
    freq_hi_mhz: float | None = None,
) -> tuple[str, str]:
    presets = available_presets()

    # 1. SIGPROC telescope_id exact match (strongest canonical signal).
    if telescope_id is not None:
        for preset in presets:
            if telescope_id in preset.telescope_ids:
                return preset.key, f"matched telescope_id={telescope_id}"

    # 2. Schema/format signature (unambiguous file-type proof).
    if schema_version:
        token = schema_version.strip()
        if token:
            for preset in presets:
                if token in preset.format_signatures:
                    return preset.key, f"matched format signature '{token}'"

    # 3. Telescope-name alias.
    if telescope_name:
        normalized = _normalize_name(telescope_name)
        if normalized:
            name_matches = [
                preset.key
                for preset in presets
                if any(_normalize_name(alias) == normalized for alias in preset.telescope_name_aliases)
            ]
            if len(name_matches) == 1:
                return name_matches[0], f"matched telescope name '{telescope_name}'"

    # 4. SIGPROC machine_id (historically weakest SIGPROC signal, unique only).
    if machine_id is not None:
        machine_matches = [preset.key for preset in presets if machine_id in preset.machine_ids]
        if len(machine_matches) == 1:
            return machine_matches[0], f"matched machine_id={machine_id}"
        if len(machine_matches) > 1:
            return "generic", f"ambiguous machine_id={machine_id}"

    # 5. Fallback: report the strongest hint we did see.
    if telescope_id is not None:
        return "generic", f"unrecognized telescope_id={telescope_id}"
    if machine_id is not None:
        return "generic", f"unrecognized machine_id={machine_id}"
    return "generic", "no matching telescope hints"


def _resolve_band_calibration(
    calibrations: tuple[ReceiverBandCalibration, ...],
    observed_lo_mhz: float,
    observed_hi_mhz: float,
) -> ReceiverBandCalibration | None:
    observed_lo_mhz, observed_hi_mhz = sorted((float(observed_lo_mhz), float(observed_hi_mhz)))
    center_mhz = 0.5 * (observed_lo_mhz + observed_hi_mhz)

    best_overlap = 0.0
    best: ReceiverBandCalibration | None = None
    for calibration in calibrations:
        overlap = calibration.overlap_mhz(observed_lo_mhz, observed_hi_mhz)
        if overlap > best_overlap:
            best_overlap = overlap
            best = calibration
        elif overlap == best_overlap and overlap > 0 and best is not None:
            if calibration.contains(center_mhz) and not best.contains(center_mhz):
                best = calibration

    if best is not None and (best_overlap > 0 or best.contains(center_mhz)):
        return best
    return None


def resolve_default_sefd_jy(
    preset_key: str | None,
    observed_lo_mhz: float,
    observed_hi_mhz: float,
) -> float | None:
    preset = get_preset(preset_key)
    if preset.sefd_jy is not None:
        return preset.sefd_jy

    if preset.key == "gbt":
        calibration = _resolve_band_calibration(GBT_BAND_CALIBRATIONS, observed_lo_mhz, observed_hi_mhz)
        return None if calibration is None else calibration.sefd_jy

    return None


@dataclass(frozen=True)
class ObservationConfig:
    dm: float
    preset_key: str = "generic"
    telescope_label: str = "Generic Filterbank"
    sefd_jy: float | None = None
    npol_override: int | None = None
    read_start_sec: float = 0.0
    read_end_sec: float | None = None
    normalization_tail_fraction: float = 0.25
    auto_mask_profile: str = "auto"
    distance_mpc: float | None = None
    redshift: float | None = None
    sefd_fractional_uncertainty: float | None = None
    distance_fractional_uncertainty: float | None = None

    @classmethod
    def from_preset(
        cls,
        dm: float,
        preset_key: str | None = "generic",
        *,
        sefd_jy: float | None = None,
        npol_override: int | None = None,
        read_start_sec: float | None = None,
        read_end_sec: float | None = None,
        auto_mask_profile: str | None = "auto",
        distance_mpc: float | None = None,
        redshift: float | None = None,
        sefd_fractional_uncertainty: float | None = None,
        distance_fractional_uncertainty: float | None = None,
    ) -> "ObservationConfig":
        preset = get_preset(preset_key)
        mask_profile = get_auto_mask_profile(auto_mask_profile)

        return cls(
            dm=float(dm),
            preset_key=preset.key,
            telescope_label=preset.label,
            sefd_jy=preset.sefd_jy if sefd_jy is None else float(sefd_jy),
            npol_override=None if npol_override is None else max(1, int(npol_override)),
            read_start_sec=preset.read_start_sec if read_start_sec is None else float(read_start_sec),
            read_end_sec=preset.read_end_sec if read_end_sec is None else float(read_end_sec),
            normalization_tail_fraction=preset.normalization_tail_fraction,
            auto_mask_profile=mask_profile.key,
            distance_mpc=distance_mpc,
            redshift=redshift,
            sefd_fractional_uncertainty=(
                None if sefd_fractional_uncertainty is None else max(0.0, float(sefd_fractional_uncertainty))
            ),
            distance_fractional_uncertainty=(
                None
                if distance_fractional_uncertainty is None
                else max(0.0, float(distance_fractional_uncertainty))
            ),
        )

    def read_start_for_file(self, filename: str) -> float:
        del filename
        return self.read_start_sec
