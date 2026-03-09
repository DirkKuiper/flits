from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException

from flits.io.filterbank import FilterbankInspection
from flits.web.app import DetectFilterbankRequest, detect_filterbank, list_filterbank_files, resolve_burst_path


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "blc_s_guppi_60385_53711_DIAG_FRB20240114A_0057_40.265_41.518_b32_I0_D527_851_F192D_K_t30_d1.fil"


class WebApiTest(unittest.TestCase):
    @unittest.skipUnless(SAMPLE.exists(), "Sample filterbank file is not available")
    def test_detect_endpoint_reports_gbt_for_sample_filterbank(self) -> None:
        payload = detect_filterbank(DetectFilterbankRequest(bfile=str(SAMPLE)))

        self.assertEqual(payload["detected_preset_key"], "gbt")
        self.assertEqual(payload["detected_preset_label"], "GBT")
        self.assertEqual(payload["telescope_id"], 6)
        self.assertEqual(payload["detection_basis"], "matched telescope_id=6")

    @unittest.skipUnless(SAMPLE.exists(), "Sample filterbank file is not available")
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


if __name__ == "__main__":
    unittest.main()
