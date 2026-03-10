from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import numpy as np
from fastapi import HTTPException

from flits.io.filterbank import FilterbankInspection, your
from flits.models import FilterbankMetadata
from flits.session import BurstSession
from flits.settings import ObservationConfig
from flits.web.app import (
    ActionRequest,
    CreateSessionRequest,
    DetectFilterbankRequest,
    SESSIONS,
    STATIC_DIR,
    auto_mask_profiles,
    create_session,
    data_dir,
    delete_session,
    detect_filterbank,
    list_filterbank_files,
    main,
    resolve_burst_path,
    session_action,
)


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "blc_s_guppi_60385_53711_DIAG_FRB20240114A_0057_40.265_41.518_b32_I0_D527_851_F192D_K_t30_d1.fil"
DM_CONST = 1 / (2.41 * 10 ** -4)


def _synthetic_session(*, auto_mask_profile: str = "auto") -> BurstSession:
    freqs = np.linspace(1100.0, 1000.0, 8)
    tsamp = 1e-3
    num_time_bins = 256
    aligned_bin = 120
    pulse = np.exp(-0.5 * ((np.arange(num_time_bins, dtype=float) - aligned_bin) / 2.5) ** 2)
    time_shift = DM_CONST * 50.0 * (float(np.max(freqs)) ** -2.0 - freqs ** -2.0)
    bin_shift = np.round(time_shift / tsamp).astype(int)

    data = np.zeros((freqs.size, num_time_bins), dtype=float)
    for chan, shift in enumerate(bin_shift):
        data[chan, :] = np.roll(pulse, -int(shift))

    metadata = FilterbankMetadata(
        source_path=Path("synthetic_dm.fil"),
        source_name="synthetic_dm",
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
        auto_mask_profile=auto_mask_profile,
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


class WebApiTest(unittest.TestCase):
    def test_auto_mask_profiles_endpoint_lists_expected_profiles(self) -> None:
        payload = auto_mask_profiles()

        self.assertEqual([profile["key"] for profile in payload["profiles"]], ["fast", "auto", "thorough"])
        self.assertEqual(payload["profiles"][1]["label"], "Auto")

    @unittest.skipUnless(SAMPLE.exists() and your.Your is not None, "Sample filterbank reader is not available")
    def test_detect_endpoint_reports_gbt_for_sample_filterbank(self) -> None:
        payload = detect_filterbank(DetectFilterbankRequest(bfile=str(SAMPLE)))

        self.assertEqual(payload["detected_preset_key"], "gbt")
        self.assertEqual(payload["detected_preset_label"], "GBT")
        self.assertEqual(payload["telescope_id"], 6)
        self.assertEqual(payload["detection_basis"], "matched telescope_id=6")

    @unittest.skipUnless(SAMPLE.exists() and your.Your is not None, "Sample filterbank reader is not available")
    @patch("flits.web.app.inspect_filterbank")
    def test_detect_endpoint_unknown_id_falls_back_to_generic(self, mock_inspect: object) -> None:
        mock_inspect.return_value = FilterbankInspection(
            source_path=SAMPLE.resolve(),
            source_name="unknown_source",
            telescope_id=999,
            machine_id=123,
            detected_preset_key="generic",
            detection_basis="unrecognized telescope_id=999",
        )

        payload = detect_filterbank(DetectFilterbankRequest(bfile=str(SAMPLE)))

        self.assertEqual(payload["detected_preset_key"], "generic")
        self.assertEqual(payload["detected_preset_label"], "Generic Filterbank")
        self.assertEqual(payload["detection_basis"], "unrecognized telescope_id=999")

    def test_detect_endpoint_missing_file_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as context:
            detect_filterbank(DetectFilterbankRequest(bfile="missing-file.fil"))

        self.assertEqual(context.exception.status_code, 404)
        self.assertIn("Filterbank file not found", context.exception.detail)

    def test_data_dir_env_controls_relative_lookup_and_listing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            nested = tmp_path / "nested"
            nested.mkdir()
            filterbank = nested / "example.fil"
            filterbank.write_bytes(b"")

            with patch.dict("os.environ", {"FLITS_DATA_DIR": str(tmp_path)}):
                self.assertEqual(list_filterbank_files(), ["nested/example.fil"])
                self.assertEqual(resolve_burst_path("nested/example.fil"), filterbank.resolve())

    def test_data_dir_defaults_to_current_working_directory(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "cwd-example.fil").write_bytes(b"")
            original_cwd = Path.cwd()
            try:
                os.chdir(tmp_path)
                with patch.dict("os.environ", {}, clear=True):
                    self.assertEqual(data_dir(), tmp_path.resolve())
                    self.assertEqual(list_filterbank_files(), ["cwd-example.fil"])
            finally:
                os.chdir(original_cwd)

    @patch("flits.web.app.uvicorn.run")
    def test_main_accepts_data_dir_flag(self, mock_run: object) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir).resolve()
            with patch.dict("os.environ", {}, clear=True):
                with patch.object(sys, "argv", ["flits", "--data-dir", str(tmp_path), "--port", "9000"]):
                    main()
                    self.assertEqual(os.environ.get("FLITS_DATA_DIR"), str(tmp_path))

            mock_run.assert_called_once_with("flits.web.app:app", host="127.0.0.1", port=9000, reload=False)

    def test_packaged_static_assets_exist(self) -> None:
        self.assertTrue((STATIC_DIR / "index.html").exists())
        self.assertTrue((STATIC_DIR / "app.js").exists())
        self.assertTrue((STATIC_DIR / "styles.css").exists())

    @patch("flits.web.app.BurstSession.from_file")
    @patch("flits.web.app.resolve_burst_path")
    def test_create_session_passes_selected_auto_mask_profile(self, mock_resolve_path: object, mock_from_file: object) -> None:
        mock_resolve_path.return_value = Path("/tmp/synthetic.fil")
        mock_from_file.return_value = _synthetic_session(auto_mask_profile="thorough")

        payload = create_session(
            CreateSessionRequest(
                bfile="synthetic.fil",
                dm=50.0,
                auto_mask_profile="thorough",
                distance_mpc=123.0,
                redshift=0.12,
            )
        )

        try:
            self.assertEqual(payload["view"]["meta"]["auto_mask_profile"], "thorough")
            self.assertEqual(payload["view"]["meta"]["auto_mask_profile_label"], "Thorough")
            mock_from_file.assert_called_once_with(
                "/tmp/synthetic.fil",
                dm=50.0,
                telescope=None,
                sefd_jy=None,
                read_start_sec=None,
                initial_crop_sec=None,
                auto_mask_profile="thorough",
                distance_mpc=123.0,
                redshift=0.12,
            )
        finally:
            SESSIONS.pop(payload["session_id"], None)

    def test_session_action_auto_mask_jess_passes_profile_override(self) -> None:
        session_id = "synthetic-mask-profile"
        session = _synthetic_session()
        session.auto_mask_jess = Mock()
        SESSIONS[session_id] = session
        try:
            session_action(
                session_id,
                ActionRequest(
                    type="auto_mask_jess",
                    payload={"profile": "fast"},
                ),
            )
        finally:
            SESSIONS.pop(session_id, None)

        session.auto_mask_jess.assert_called_once_with("fast")

    def test_session_action_optimize_dm_returns_dm_optimization_payload(self) -> None:
        session_id = "synthetic-dm"
        SESSIONS[session_id] = _synthetic_session()
        try:
            payload = session_action(
                session_id,
                ActionRequest(
                    type="optimize_dm",
                    payload={"center_dm": 50.0, "half_range": 4.0, "step": 0.5},
                ),
            )
        finally:
            SESSIONS.pop(session_id, None)

        optimization = payload["view"]["dm_optimization"]
        self.assertIsNotNone(optimization)
        self.assertEqual(optimization["center_dm"], 50.0)
        self.assertEqual(optimization["step"], 0.5)
        self.assertIn("trial_dms", optimization)
        self.assertIn("best_dm", optimization)
        self.assertEqual(len(optimization["trial_dms"]), len(optimization["snr"]))

    def test_session_action_compute_properties_returns_nested_science_payload(self) -> None:
        session_id = "synthetic-measurements"
        session = _synthetic_session()
        session.add_region_ms(session.bin_to_ms(116), session.bin_to_ms(124))
        SESSIONS[session_id] = session
        try:
            payload = session_action(
                session_id,
                ActionRequest(
                    type="compute_properties",
                    payload={},
                ),
            )
        finally:
            SESSIONS.pop(session_id, None)

        results = payload["view"]["results"]
        self.assertIsNotNone(results)
        self.assertIn("toa_topo_mjd", results)
        self.assertIn("measurement_flags", results)
        self.assertIn("uncertainties", results)
        self.assertIn("provenance", results)
        self.assertIn("diagnostics", results)
        self.assertIn("mjd_at_peak", results)

    def test_delete_session_removes_session(self) -> None:
        session_id = "synthetic-delete"
        SESSIONS[session_id] = _synthetic_session()

        payload = delete_session(session_id)

        self.assertEqual(payload, {"status": "deleted"})
        self.assertNotIn(session_id, SESSIONS)


if __name__ == "__main__":
    unittest.main()
