from __future__ import annotations

import io
import json
import struct
import unittest
from pathlib import Path

import numpy as np
from fastapi import HTTPException

from flits.models import FilterbankMetadata
from flits.session import BurstSession
from flits.settings import ObservationConfig
from flits.web.app import ActionRequest, SESSIONS, session_action, session_export_artifact, session_export_manifest


DM_CONST = 1 / (2.41 * 10 ** -4)
_SIGPROC_FIELD_TYPES = {
    "rawdatafile": "string",
    "source_name": "string",
    "machine_id": "int",
    "barycentric": "int",
    "pulsarcentric": "int",
    "telescope_id": "int",
    "src_raj": "double",
    "src_dej": "double",
    "az_start": "double",
    "za_start": "double",
    "data_type": "int",
    "fch1": "double",
    "foff": "double",
    "nchans": "int",
    "nbeams": "int",
    "ibeam": "int",
    "nbits": "int",
    "tstart": "double",
    "tsamp": "double",
    "nifs": "int",
}


def _synthetic_export_session(*, num_channels: int = 24, true_dm: float = 50.0) -> BurstSession:
    freqs = np.linspace(1100.0, 1000.0, num_channels)
    tsamp = 1e-3
    num_time_bins = 256
    aligned_bin = 120

    time = np.arange(num_time_bins, dtype=float)
    pulse = np.exp(-0.5 * ((time - aligned_bin) / 2.5) ** 2)
    reffreq = float(np.max(freqs))
    time_shift = DM_CONST * true_dm * (reffreq ** -2.0 - freqs ** -2.0)
    bin_shift = np.round(time_shift / tsamp).astype(int)
    rng = np.random.default_rng(67890)

    data = np.zeros((freqs.size, num_time_bins), dtype=float)
    for chan, shift in enumerate(bin_shift):
        data[chan, :] = np.roll(pulse, -int(shift))
    data += rng.normal(0.0, 0.03, size=data.shape)

    metadata = FilterbankMetadata(
        source_path=Path("synthetic_export.fil"),
        source_name="synthetic_export",
        tsamp=tsamp,
        freqres=float(abs(freqs[1] - freqs[0])),
        start_mjd=60000.0,
        read_start_sec=0.0,
        sefd_jy=10.0,
        bandwidth_mhz=float(abs(freqs[1] - freqs[0]) * freqs.size),
        npol=1,
        freqs_mhz=freqs,
        header_npol=1,
        telescope_id=None,
        machine_id=None,
        detected_preset_key="generic",
        detection_basis="synthetic",
    )
    config = ObservationConfig.from_preset(
        dm=0.0,
        preset_key="generic",
        sefd_jy=10.0,
        distance_mpc=150.0,
        redshift=0.05,
    )
    return BurstSession(
        config=config,
        metadata=metadata,
        data=data,
        crop_start=0,
        crop_end=num_time_bins,
        event_start=aligned_bin - 8,
        event_end=aligned_bin + 8,
        spec_ex_lo=0,
        spec_ex_hi=freqs.size - 1,
        channel_mask=np.zeros(freqs.size, dtype=bool),
    )


def _sigproc_string(buffer: io.BytesIO) -> str:
    size = struct.unpack("i", buffer.read(4))[0]
    return buffer.read(size).decode("utf-8")


def _read_sigproc_header(payload: bytes) -> tuple[dict[str, object], int]:
    buffer = io.BytesIO(payload)
    if _sigproc_string(buffer) != "HEADER_START":
        raise AssertionError("Missing SIGPROC header start")
    header: dict[str, object] = {}
    while True:
        name = _sigproc_string(buffer)
        if name == "HEADER_END":
            break
        field_type = _SIGPROC_FIELD_TYPES[name]
        if field_type == "string":
            header[name] = _sigproc_string(buffer)
        elif field_type == "int":
            header[name] = struct.unpack("i", buffer.read(4))[0]
        else:
            header[name] = struct.unpack("d", buffer.read(8))[0]
    return header, buffer.tell()


class ExportResultsTest(unittest.TestCase):
    def tearDown(self) -> None:
        SESSIONS.clear()

    def test_preview_export_results_without_selection_returns_empty_preview_and_no_snapshot(self) -> None:
        session_id = "synthetic-export-preview-empty"
        session = _synthetic_export_session()
        SESSIONS[session_id] = session

        payload = session_action(
            session_id,
            ActionRequest(type="preview_export_results", payload={"include": [], "plot_formats": []}),
        )

        preview = payload["export_preview"]
        self.assertIsNone(payload["export_manifest"])
        self.assertEqual(
            preview["selection"],
            {"include": [], "plot_formats": [], "window_formats": [], "window_resolutions": []},
        )
        self.assertEqual(preview["artifacts"], [])
        self.assertEqual(preview["plot_previews"], [])
        self.assertFalse(session.export_snapshots)

    def test_preview_export_results_returns_exact_artifacts_and_plot_thumbnails(self) -> None:
        session_id = "synthetic-export-preview"
        session = _synthetic_export_session()
        session.compute_properties()
        session.optimize_dm(center_dm=50.0, half_range=4.0, step=0.5)
        SESSIONS[session_id] = session

        payload = session_action(
            session_id,
            ActionRequest(type="preview_export_results", payload={"include": ["json", "plots"], "plot_formats": ["png"]}),
        )

        preview = payload["export_preview"]
        self.assertEqual(
            preview["selection"],
            {
                "include": ["json", "plots"],
                "plot_formats": ["png"],
                "window_formats": [],
                "window_resolutions": [],
            },
        )
        labels = {artifact["label"] for artifact in preview["artifacts"]}
        self.assertIn("Science JSON", labels)
        self.assertIn("Dynamic Spectrum (PNG)", labels)
        self.assertIn("DM Curve (PNG)", labels)
        self.assertIn("Power Spectrum (PNG)", labels)
        self.assertEqual(sum(1 for artifact in preview["artifacts"] if artifact["kind"] == "plot"), 6)

        plot_previews = {item["plot_key"]: item for item in preview["plot_previews"]}
        self.assertEqual(set(plot_previews), {"dynamic_spectrum", "profile_diagnostics", "acf_panel", "power_spectrum", "dm_curve", "dm_residuals"})
        self.assertTrue(plot_previews["dynamic_spectrum"]["svg"].lstrip().startswith("<svg"))
        self.assertEqual(plot_previews["dm_curve"]["status"], "ready")
        self.assertEqual(plot_previews["power_spectrum"]["status"], "omitted")
        self.assertFalse(session.export_snapshots)

    def test_preview_omissions_match_built_export_selection(self) -> None:
        session_id = "synthetic-export-preview-omitted"
        session = _synthetic_export_session()
        SESSIONS[session_id] = session

        preview = session_action(
            session_id,
            ActionRequest(type="preview_export_results", payload={"include": ["plots"], "plot_formats": ["png"]}),
        )["export_preview"]
        preview_artifacts = {artifact["label"]: artifact for artifact in preview["artifacts"]}
        self.assertEqual(preview_artifacts["DM Curve (PNG)"]["status"], "omitted")
        self.assertEqual(preview_artifacts["DM Curve (PNG)"]["reason"], "dm_optimization_unavailable")
        self.assertEqual(preview_artifacts["DM Residuals (PNG)"]["reason"], "dm_optimization_unavailable")
        self.assertEqual(preview_artifacts["Power Spectrum (PNG)"]["reason"], "temporal_structure_unavailable")

        manifest = session_action(
            session_id,
            ActionRequest(type="export_results", payload={"include": ["plots"], "plot_formats": ["png"]}),
        )["export_manifest"]
        artifact_by_name = {artifact["name"]: artifact for artifact in manifest["artifacts"]}
        dm_curve = next(artifact for name, artifact in artifact_by_name.items() if name.endswith("_dm_curve.png"))
        dm_residuals = next(artifact for name, artifact in artifact_by_name.items() if name.endswith("_dm_residuals.png"))
        power_spectrum = next(artifact for name, artifact in artifact_by_name.items() if name.endswith("_power_spectrum.png"))
        self.assertEqual(dm_curve["status"], "omitted")
        self.assertEqual(dm_curve["reason"], "dm_optimization_unavailable")
        self.assertEqual(dm_residuals["reason"], "dm_optimization_unavailable")
        self.assertEqual(power_spectrum["reason"], "temporal_structure_unavailable")

    def test_build_after_preview_uses_selected_export_set(self) -> None:
        session_id = "synthetic-export-preview-build"
        session = _synthetic_export_session()
        session.compute_properties()
        session.optimize_dm(center_dm=50.0, half_range=4.0, step=0.5)
        SESSIONS[session_id] = session

        preview = session_action(
            session_id,
            ActionRequest(type="preview_export_results", payload={"include": ["json", "plots"], "plot_formats": ["png"]}),
        )["export_preview"]
        manifest = session_action(
            session_id,
            ActionRequest(type="export_results", payload={"include": ["json", "plots"], "plot_formats": ["png"]}),
        )["export_manifest"]

        preview_labels = {artifact["label"] for artifact in preview["artifacts"]}
        artifact_names = {artifact["name"] for artifact in manifest["artifacts"]}
        self.assertEqual(len(manifest["artifacts"]), len(preview["artifacts"]))
        self.assertTrue(any(name.endswith("_science.json") for name in artifact_names))
        self.assertTrue(any(name.endswith("_dynamic_spectrum.png") for name in artifact_names))
        self.assertFalse(any(name.endswith("_catalog.csv") for name in artifact_names))
        self.assertFalse(any(name.endswith("_diagnostics.npz") for name in artifact_names))
        self.assertIn("Science JSON", preview_labels)
        self.assertIn("Profile Diagnostics (PNG)", preview_labels)

    def test_window_export_preview_and_artifacts_cover_native_and_view_modes(self) -> None:
        session_id = "synthetic-window-export"
        session = _synthetic_export_session()
        session.set_crop_ms(session.bin_to_ms(105), session.bin_to_ms(140))
        session.set_spectral_extent_freq(float(session.freqs[4]), float(session.freqs[19]))
        session.mask_channel_freq(float(session.freqs[6]))
        session.set_time_factor(4)
        session.set_freq_factor(3)
        SESSIONS[session_id] = session

        payload = {"include": ["window"], "window_formats": ["npz", "fil"], "window_resolutions": ["native", "view"]}
        preview = session_action(
            session_id,
            ActionRequest(type="preview_export_results", payload=payload),
        )["export_preview"]

        self.assertEqual(
            preview["selection"],
            {
                "include": ["window"],
                "plot_formats": [],
                "window_formats": ["npz", "fil"],
                "window_resolutions": ["native", "view"],
            },
        )
        preview_labels = {artifact["label"] for artifact in preview["artifacts"]}
        self.assertIn("Window Metadata (Native)", preview_labels)
        self.assertIn("Window Metadata (View)", preview_labels)
        self.assertIn("Window Data (NPZ, Native)", preview_labels)
        self.assertIn("Window Data (NPZ, View)", preview_labels)
        self.assertIn("Window Filterbank (FIL, Native)", preview_labels)
        self.assertIn("Window Filterbank (FIL, View)", preview_labels)
        self.assertEqual(preview["plot_previews"], [])

        manifest = session_action(
            session_id,
            ActionRequest(type="export_results", payload=payload),
        )["export_manifest"]
        artifact_names = {artifact["name"] for artifact in manifest["artifacts"]}
        self.assertTrue(any(name.endswith("_window_native.meta.json") for name in artifact_names))
        self.assertTrue(any(name.endswith("_window_view.meta.json") for name in artifact_names))
        self.assertTrue(any(name.endswith("_window_native.npz") for name in artifact_names))
        self.assertTrue(any(name.endswith("_window_view.npz") for name in artifact_names))
        self.assertTrue(any(name.endswith("_window_native.fil") for name in artifact_names))
        self.assertTrue(any(name.endswith("_window_view.fil") for name in artifact_names))

        native_meta_name = next(name for name in artifact_names if name.endswith("_window_native.meta.json"))
        native_meta = json.loads(session_export_artifact(session_id, manifest["export_id"], native_meta_name).body.decode("utf-8"))
        self.assertEqual(native_meta["window"]["time_bins"], [105, 140])
        self.assertEqual(native_meta["window"]["event_window_bins"], [7, 23])
        self.assertEqual(native_meta["window"]["spectral_extent_channels"], [4, 19])
        self.assertEqual(native_meta["window"]["masked_channels"], [6])

        native_npz_name = next(name for name in artifact_names if name.endswith("_window_native.npz"))
        native_npz_response = session_export_artifact(session_id, manifest["export_id"], native_npz_name)
        with np.load(io.BytesIO(native_npz_response.body)) as arrays:
            self.assertEqual(arrays["dynamic_spectrum"].shape, (16, 35))
            self.assertTrue(np.isnan(arrays["dynamic_spectrum"][2]).all())
            self.assertEqual(arrays["time_bins"].tolist(), [105, 140])
            self.assertEqual(arrays["crop_bins"].tolist(), [105, 140])
            self.assertEqual(arrays["event_window_bins"].tolist(), [7, 23])
            self.assertEqual(arrays["spectral_extent_channels"].tolist(), [4, 19])
            self.assertEqual(arrays["masked_channels"].tolist(), [6])
            self.assertEqual(arrays["time_factor"].tolist(), [1])
            self.assertEqual(arrays["freq_factor"].tolist(), [1])
            self.assertAlmostEqual(float(arrays["effective_tsamp_sec"][0]), 0.001, places=9)

        view_npz_name = next(name for name in artifact_names if name.endswith("_window_view.npz"))
        view_npz_response = session_export_artifact(session_id, manifest["export_id"], view_npz_name)
        with np.load(io.BytesIO(view_npz_response.body)) as arrays:
            self.assertEqual(arrays["dynamic_spectrum"].shape, (6, 8))
            self.assertEqual(arrays["time_bins"].tolist(), [105, 137])
            self.assertEqual(arrays["event_window_bins"].tolist(), [1, 6])
            self.assertEqual(arrays["time_factor"].tolist(), [4])
            self.assertEqual(arrays["freq_factor"].tolist(), [3])
            self.assertAlmostEqual(float(arrays["effective_tsamp_sec"][0]), 0.004, places=9)

        native_fil_name = next(name for name in artifact_names if name.endswith("_window_native.fil"))
        native_fil_response = session_export_artifact(session_id, manifest["export_id"], native_fil_name)
        header, offset = _read_sigproc_header(native_fil_response.body)
        self.assertEqual(header["nbits"], 32)
        self.assertEqual(header["nchans"], 16)
        self.assertAlmostEqual(float(header["tsamp"]), 0.001, places=9)
        self.assertAlmostEqual(float(header["fch1"]), float(session.freqs[4]), places=9)
        self.assertLess(float(header["foff"]), 0.0)
        expected_tstart = 60000.0 + ((105 * 0.001) / 86400.0)
        self.assertAlmostEqual(float(header["tstart"]), expected_tstart, places=12)
        spectra = np.frombuffer(native_fil_response.body[offset:], dtype=np.float32).reshape(35, 16).T
        self.assertTrue(np.allclose(spectra[2], 0.0))

    def test_export_results_builds_manifest_and_downloadable_artifacts(self) -> None:
        session_id = "synthetic-export"
        session = _synthetic_export_session()
        session.set_time_factor(8)
        session.set_freq_factor(2)
        session.add_region_ms(session.bin_to_ms(116), session.bin_to_ms(124))
        session.compute_properties()
        session.optimize_dm(center_dm=50.0, half_range=4.0, step=0.5)
        session.run_temporal_structure_analysis(16.0)
        SESSIONS[session_id] = session

        payload = session_action(
            session_id,
            ActionRequest(type="export_results", payload={}),
        )

        manifest = payload["export_manifest"]
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["schema_version"], "1.4")
        artifact_names = {artifact["name"] for artifact in manifest["artifacts"]}
        self.assertTrue(any(name.endswith("_science.json") for name in artifact_names))
        self.assertTrue(any(name.endswith("_catalog.csv") for name in artifact_names))
        self.assertTrue(any(name.endswith("_diagnostics.npz") for name in artifact_names))
        self.assertTrue(any(name.endswith("_dynamic_spectrum.png") for name in artifact_names))
        self.assertTrue(any(name.endswith("_dynamic_spectrum.svg") for name in artifact_names))
        self.assertTrue(any(name.endswith("_power_spectrum.png") for name in artifact_names))
        self.assertTrue(any(name.endswith("_power_spectrum.svg") for name in artifact_names))
        self.assertTrue(any(name.endswith("_dm_curve.png") for name in artifact_names))
        self.assertTrue(any(name.endswith("_dm_residuals.svg") for name in artifact_names))

        manifest_payload = session_export_manifest(session_id, manifest["export_id"])
        self.assertEqual(manifest_payload["export_id"], manifest["export_id"])

        json_name = next(name for name in artifact_names if name.endswith("_science.json"))
        json_response = session_export_artifact(session_id, manifest["export_id"], json_name)
        science = json.loads(json_response.body.decode("utf-8"))
        self.assertIn("schema_version", science)
        self.assertIn("flits_version", science)
        self.assertIn("meta", science)
        self.assertIn("state", science)
        self.assertIn("results", science)
        self.assertIn("dm_optimization", science)
        self.assertIn("temporal_structure", science)
        self.assertIn("artifacts", science)
        self.assertNotIn("plot", science)
        self.assertIsNotNone(science["temporal_structure"])
        self.assertIn("min_structure_ms_primary", science["temporal_structure"])
        self.assertIn("power_law_fit_status", science["temporal_structure"])
        self.assertIn("crossover_frequency_status", science["temporal_structure"])
        self.assertIn("noise_psd_freq_hz", science["temporal_structure"])

        csv_name = next(name for name in artifact_names if name.endswith("_catalog.csv"))
        csv_response = session_export_artifact(session_id, manifest["export_id"], csv_name)
        csv_text = csv_response.body.decode("utf-8")
        self.assertIn("iso_e_erg", csv_text.splitlines()[0])
        self.assertIn("dm_best", csv_text.splitlines()[0])
        self.assertIn("min_structure_ms_primary", csv_text.splitlines()[0])
        self.assertIn("psd_fit_status", csv_text.splitlines()[0])
        self.assertIn("psd_crossover_frequency_hz", csv_text.splitlines()[0])
        self.assertIn("noise_psd_segment_count", csv_text.splitlines()[0])
        self.assertIn("npol", csv_text.splitlines()[0])

        npz_name = next(name for name in artifact_names if name.endswith("_diagnostics.npz"))
        npz_response = session_export_artifact(session_id, manifest["export_id"], npz_name)
        with np.load(io.BytesIO(npz_response.body)) as arrays:
            self.assertIn("dynamic_spectrum", arrays.files)
            self.assertIn("trial_dms", arrays.files)
            self.assertIn("raw_periodogram_freq_hz", arrays.files)
            self.assertIn("averaged_psd_power", arrays.files)
            self.assertIn("crossover_frequency_hz", arrays.files)
            self.assertIn("noise_psd_power", arrays.files)
            self.assertIn("matched_filter_scales_ms", arrays.files)
            self.assertIn("wavelet_sigma", arrays.files)
            self.assertEqual(
                arrays["dynamic_spectrum"].shape,
                np.asarray(payload["view"]["plot"]["heatmap"]["z"]).shape,
            )

        session.compute_properties()
        session.fit_scattering()
        fit_manifest = session_action(
            session_id,
            ActionRequest(type="export_results", payload={}),
        )["export_manifest"]
        fit_npz_name = next(
            artifact["name"] for artifact in fit_manifest["artifacts"] if artifact["name"].endswith("_diagnostics.npz")
        )
        fit_npz_response = session_export_artifact(session_id, fit_manifest["export_id"], fit_npz_name)
        with np.load(io.BytesIO(fit_npz_response.body)) as arrays:
            self.assertIn("scattering_data_dynamic_spectrum_sn", arrays.files)
            self.assertIn("scattering_model_dynamic_spectrum_sn", arrays.files)
            self.assertIn("scattering_residual_dynamic_spectrum_sn", arrays.files)
            self.assertIn("scattering_freq_axis_mhz", arrays.files)

        png_name = next(name for name in artifact_names if name.endswith("_dynamic_spectrum.png"))
        svg_name = next(name for name in artifact_names if name.endswith("_dynamic_spectrum.svg"))
        png_response = session_export_artifact(session_id, manifest["export_id"], png_name)
        svg_response = session_export_artifact(session_id, manifest["export_id"], svg_name)
        self.assertEqual(png_response.media_type, "image/png")
        self.assertEqual(svg_response.media_type, "image/svg+xml")
        self.assertGreater(len(png_response.body), 0)
        self.assertGreater(len(svg_response.body), 0)

    def test_export_dm_curve_uses_selected_metric_label(self) -> None:
        session_id = "synthetic-export-dmphase"
        session = _synthetic_export_session()
        session.optimize_dm(center_dm=50.0, half_range=4.0, step=0.5, metric="dm_phase")
        SESSIONS[session_id] = session

        manifest = session_action(
            session_id,
            ActionRequest(type="export_results", payload={"include": ["plots"], "plot_formats": ["svg"]}),
        )["export_manifest"]

        dm_curve_name = next(
            artifact["name"] for artifact in manifest["artifacts"] if artifact["name"].endswith("_dm_curve.svg")
        )
        svg_text = session_export_artifact(session_id, manifest["export_id"], dm_curve_name).body.decode("utf-8")
        self.assertIn("DMphase", svg_text)

    def test_export_results_handles_missing_measurements_and_dm_with_explicit_omissions(self) -> None:
        session_id = "synthetic-export-empty"
        session = _synthetic_export_session()
        SESSIONS[session_id] = session

        payload = session_action(
            session_id,
            ActionRequest(type="export_results", payload={}),
        )

        manifest = payload["export_manifest"]
        artifacts = {artifact["name"]: artifact for artifact in manifest["artifacts"]}
        omitted_dm_curve = next(artifact for artifact in artifacts.values() if artifact["name"].endswith("_dm_curve.png"))
        omitted_dm_residuals = next(artifact for artifact in artifacts.values() if artifact["name"].endswith("_dm_residuals.svg"))
        self.assertEqual(omitted_dm_curve["status"], "omitted")
        self.assertEqual(omitted_dm_curve["reason"], "dm_optimization_unavailable")
        self.assertEqual(omitted_dm_residuals["status"], "omitted")
        self.assertEqual(omitted_dm_residuals["reason"], "dm_optimization_unavailable")

        json_name = next(name for name in artifacts if name.endswith("_science.json"))
        science = json.loads(session_export_artifact(session_id, manifest["export_id"], json_name).body.decode("utf-8"))
        self.assertIsNone(science["results"])
        self.assertIsNone(science["dm_optimization"])

        with self.assertRaises(HTTPException) as context:
            session_export_artifact(session_id, manifest["export_id"], omitted_dm_curve["name"])
        self.assertEqual(context.exception.status_code, 404)

    def test_export_snapshot_is_immutable_after_session_changes(self) -> None:
        session_id = "synthetic-export-immutable"
        session = _synthetic_export_session()
        session.add_region_ms(session.bin_to_ms(116), session.bin_to_ms(124))
        session.compute_properties()
        SESSIONS[session_id] = session

        manifest = session_action(session_id, ActionRequest(type="export_results", payload={}))["export_manifest"]
        json_name = next(artifact["name"] for artifact in manifest["artifacts"] if artifact["name"].endswith("_science.json"))
        first_json = session_export_artifact(session_id, manifest["export_id"], json_name).body

        session.set_crop_ms(10.0, 180.0)
        session.set_dm(51.0)

        second_json = session_export_artifact(session_id, manifest["export_id"], json_name).body
        self.assertEqual(first_json, second_json)

    def test_export_snapshot_eviction_removes_oldest_bundle(self) -> None:
        session_id = "synthetic-export-evict"
        session = _synthetic_export_session()
        SESSIONS[session_id] = session

        export_ids: list[str] = []
        for offset in range(4):
            session.set_event_ms(session.bin_to_ms(110 + offset), session.bin_to_ms(126 + offset))
            payload = session_action(session_id, ActionRequest(type="export_results", payload={}))
            export_ids.append(payload["export_manifest"]["export_id"])

        with self.assertRaises(HTTPException) as context:
            session_export_manifest(session_id, export_ids[0])
        self.assertEqual(context.exception.status_code, 404)

        newest_manifest = session_export_manifest(session_id, export_ids[-1])
        self.assertEqual(newest_manifest["export_id"], export_ids[-1])


if __name__ == "__main__":
    unittest.main()
