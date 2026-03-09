from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TelescopePreset:
    key: str
    label: str
    sefd_jy: float | None = None
    read_start_sec: float = 0.0
    initial_crop_sec: float | None = None
    normalization_tail_fraction: float = 0.25
    use_filename_peak_time: bool = False
    nrt_500ms_read_start_sec: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "sefd_jy": self.sefd_jy,
            "read_start_sec": self.read_start_sec,
            "initial_crop_sec": self.initial_crop_sec,
            "normalization_tail_fraction": self.normalization_tail_fraction,
            "use_filename_peak_time": self.use_filename_peak_time,
        }


PRESETS: dict[str, TelescopePreset] = {
    "generic": TelescopePreset(
        key="generic",
        label="Generic Filterbank",
        sefd_jy=None,
        read_start_sec=0.0,
        initial_crop_sec=None,
        normalization_tail_fraction=0.25,
        use_filename_peak_time=False,
    ),
    "nrt": TelescopePreset(
        key="nrt",
        label="NRT",
        sefd_jy=35.0 / 1.4,
        read_start_sec=0.05,
        initial_crop_sec=0.151,
        normalization_tail_fraction=0.25,
        use_filename_peak_time=True,
        nrt_500ms_read_start_sec=0.45,
    ),
}


def available_presets() -> list[TelescopePreset]:
    return [PRESETS[key] for key in sorted(PRESETS)]


@dataclass(frozen=True)
class ObservationConfig:
    dm: float
    preset_key: str = "generic"
    telescope_label: str = "Generic Filterbank"
    sefd_jy: float | None = None
    read_start_sec: float = 0.0
    initial_crop_sec: float | None = None
    normalization_tail_fraction: float = 0.25
    use_filename_peak_time: bool = False
    nrt_500ms_read_start_sec: float | None = None
    distance_mpc: float | None = None
    redshift: float | None = None

    @classmethod
    def from_preset(
        cls,
        dm: float,
        preset_key: str = "generic",
        *,
        sefd_jy: float | None = None,
        read_start_sec: float | None = None,
        initial_crop_sec: float | None = None,
        distance_mpc: float | None = None,
        redshift: float | None = None,
    ) -> "ObservationConfig":
        preset = PRESETS.get(preset_key.lower())
        if preset is None:
            valid = ", ".join(sorted(PRESETS))
            raise ValueError(f"Unknown preset '{preset_key}'. Valid presets: {valid}.")

        return cls(
            dm=float(dm),
            preset_key=preset.key,
            telescope_label=preset.label,
            sefd_jy=preset.sefd_jy if sefd_jy is None else float(sefd_jy),
            read_start_sec=preset.read_start_sec if read_start_sec is None else float(read_start_sec),
            initial_crop_sec=preset.initial_crop_sec if initial_crop_sec is None else float(initial_crop_sec),
            normalization_tail_fraction=preset.normalization_tail_fraction,
            use_filename_peak_time=preset.use_filename_peak_time,
            nrt_500ms_read_start_sec=preset.nrt_500ms_read_start_sec,
            distance_mpc=distance_mpc,
            redshift=redshift,
        )

    def read_start_for_file(self, filename: str) -> float:
        if (
            self.preset_key == "nrt"
            and self.nrt_500ms_read_start_sec is not None
            and "500ms" in filename.lower()
        ):
            return self.nrt_500ms_read_start_sec
        return self.read_start_sec
