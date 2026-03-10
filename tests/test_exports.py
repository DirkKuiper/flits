from __future__ import annotations

import io
import json
import unittest
from pathlib import Path

import numpy as np
from fastapi import HTTPException

from flits.models import FilterbankMetadata
from flits.session import BurstSession
from flits.settings import ObservationConfig
from flits.web.app import ActionRequest, SESSIONS, session_action, session_export_artifact, session_export_manifest


DM_CONST = 1 / (2.41 * 10 ** -4)


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

    data = np.zeros((freqs.size, num_time_bins), dtype=float)
    for chan, shift in enumerate(bin_shift):
        data[chan, :] = np.roll(pulse, -int(shift))

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


class ExportResultsTest(unittest.TestCase):
    def tearDown(self) -> None:
        SESSIONS.clear()

    def test_export_results_builds_manifest_and_downloadable_artifacts(self) -> None:
        session_id = "synthetic-export"
        session = _synthetic_export_session()
        session.set_time_factor(8)
        session.set_freq_factor(2)
        session.add_region_ms(session.bin_to_ms(116), session.bin_to_ms(124))
        session.compute_properties()
        session.optimize_dm(center_dm=50.0, half_range=4.0, step=0.5)
        SESSIONS[session_id] = session

        payload = session_action(
            session_id,
            ActionRequest(type="export_results", payload={}),
        )

        manifest = payload["export_manifest"]
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["schema_version"], "1.0")
        artifact_names = {artifact["name"] for artifact in manifest["artifacts"]}
        self.assertTrue(any(name.endswith("_science.json") for name in artifact_names))
        self.assertTrue(any(name.endswith("_catalog.csv") for name in artifact_names))
        self.assertTrue(any(name.endswith("_diagnostics.npz") for name in artifact_names))
        self.assertTrue(any(name.endswith("_dynamic_spectrum.png") for name in artifact_names))
        self.assertTrue(any(name.endswith("_dynamic_spectrum.svg") for name in artifact_names))
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
        self.assertIn("artifacts", science)
        self.assertNotIn("plot", science)

        csv_name = next(name for name in artifact_names if name.endswith("_catalog.csv"))
        csv_response = session_export_artifact(session_id, manifest["export_id"], csv_name)
        csv_text = csv_response.body.decode("utf-8")
        self.assertIn("iso_e_erg", csv_text.splitlines()[0])
        self.assertIn("dm_best", csv_text.splitlines()[0])

        npz_name = next(name for name in artifact_names if name.endswith("_diagnostics.npz"))
        npz_response = session_export_artifact(session_id, manifest["export_id"], npz_name)
        with np.load(io.BytesIO(npz_response.body)) as arrays:
            self.assertIn("dynamic_spectrum", arrays.files)
            self.assertIn("trial_dms", arrays.files)
            self.assertEqual(arrays["dynamic_spectrum"].shape, (session.total_channels, session.total_time_bins))
            self.assertNotEqual(
                arrays["dynamic_spectrum"].shape,
                np.asarray(payload["view"]["plot"]["heatmap"]["z"]).shape,
            )

        png_name = next(name for name in artifact_names if name.endswith("_dynamic_spectrum.png"))
        svg_name = next(name for name in artifact_names if name.endswith("_dynamic_spectrum.svg"))
        png_response = session_export_artifact(session_id, manifest["export_id"], png_name)
        svg_response = session_export_artifact(session_id, manifest["export_id"], svg_name)
        self.assertEqual(png_response.media_type, "image/png")
        self.assertEqual(svg_response.media_type, "image/svg+xml")
        self.assertGreater(len(png_response.body), 0)
        self.assertGreater(len(svg_response.body), 0)

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
