from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import h5py
import numpy as np
from fastapi import HTTPException

from flits.io.filterbank import FilterbankInspection, your
from flits.models import FilterbankMetadata, SpectralAnalysisResult, TemporalStructureResult, UncertaintyDetail
from flits.session import BurstSession
from flits.settings import ObservationConfig
from flits.web.app import (
    ActionRequest,
    CreateSessionRequest,
    DetectFilterbankRequest,
    ImportSessionRequest,
    SESSIONS,
    STATIC_DIR,
    auto_mask_profiles,
    create_session,
    data_dir,
    delete_session,
    detect_filterbank,
    files,
    import_session,
    list_filterbank_directories,
    list_filterbank_files,
    main,
    resolve_burst_path,
    session_action,
    session_snapshot,
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
    rng = np.random.default_rng(54321)

    data = np.zeros((freqs.size, num_time_bins), dtype=float)
    for chan, shift in enumerate(bin_shift):
        data[chan, :] = np.roll(pulse, -int(shift))
    data += rng.normal(0.0, 0.03, size=data.shape)

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
        self.assertIsNone(payload["coherent_dm"])
        self.assertIsNone(payload["suggested_dm"])
        self.assertIsNone(payload["dm_guidance"])

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

    @patch("flits.web.app.inspect_filterbank")
    @patch("flits.web.app.resolve_burst_path")
    def test_detect_endpoint_reports_bbdata_dm_guidance(self, mock_resolve_path: object, mock_inspect: object) -> None:
        burst_path = Path("/tmp/beamformed.h5")
        mock_resolve_path.return_value = burst_path
        mock_inspect.return_value = FilterbankInspection(
            source_path=burst_path,
            source_name="FRB20181231C",
            telescope_id=None,
            machine_id=None,
            detected_preset_key="chime",
            detection_basis="matched format signature 'chime_bbdata_beamformed_v1'",
            telescope_name="CHIME/FRB",
            schema_version="chime_bbdata_beamformed_v1",
            coherent_dm=556.1811152206744,
        )

        payload = detect_filterbank(DetectFilterbankRequest(bfile=str(burst_path)))

        self.assertEqual(payload["coherent_dm"], 556.1811152206744)
        self.assertEqual(payload["suggested_dm"], 556.1811152206744)
        self.assertIn("residual DM", payload["dm_guidance"])

    def test_data_dir_env_controls_relative_lookup_and_listing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "root_example.fil").write_bytes(b"")
            nested = tmp_path / "nested"
            nested.mkdir()
            filterbank = nested / "example.fil"
            filterbank.write_bytes(b"")
            deeper = nested / "deeper"
            deeper.mkdir()
            deeper_filterbank = deeper / "deep_example.fil"
            deeper_filterbank.write_bytes(b"")

            with patch.dict("os.environ", {"FLITS_DATA_DIR": str(tmp_path)}):
                self.assertEqual(
                    list_filterbank_files(),
                    ["nested/deeper/deep_example.fil", "nested/example.fil", "root_example.fil"],
                )
                self.assertEqual(
                    list_filterbank_directories(),
                    [
                        {
                            "path": "",
                            "label": "Data root",
                            "file_count": 1,
                            "files": [{"path": "root_example.fil", "name": "root_example.fil"}],
                        },
                        {
                            "path": "nested",
                            "label": "nested",
                            "file_count": 1,
                            "files": [{"path": "nested/example.fil", "name": "example.fil"}],
                        },
                        {
                            "path": "nested/deeper",
                            "label": "nested/deeper",
                            "file_count": 1,
                            "files": [{"path": "nested/deeper/deep_example.fil", "name": "deep_example.fil"}],
                        },
                    ],
                )
                self.assertEqual(
                    files(),
                    {
                        "files": ["nested/deeper/deep_example.fil", "nested/example.fil", "root_example.fil"],
                        "directories": [
                            {
                                "path": "",
                                "label": "Data root",
                                "file_count": 1,
                                "files": [{"path": "root_example.fil", "name": "root_example.fil"}],
                            },
                            {
                                "path": "nested",
                                "label": "nested",
                                "file_count": 1,
                                "files": [{"path": "nested/example.fil", "name": "example.fil"}],
                            },
                            {
                                "path": "nested/deeper",
                                "label": "nested/deeper",
                                "file_count": 1,
                                "files": [{"path": "nested/deeper/deep_example.fil", "name": "deep_example.fil"}],
                            },
                        ],
                    },
                )
                self.assertEqual(resolve_burst_path("nested/example.fil"), filterbank.resolve())
                self.assertEqual(resolve_burst_path("nested/deeper/deep_example.fil"), deeper_filterbank.resolve())

    def test_listing_filters_unsupported_container_formats(self) -> None:
        from astropy.io import fits

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            supported_h5 = tmp_path / "supported.h5"
            unsupported_h5 = tmp_path / "unsupported.h5"
            unsupported_fits = tmp_path / "unsupported.fits"

            with h5py.File(supported_h5, "w") as fh:
                fh.attrs["schema_version"] = "flits_chime_v1"
                fh.attrs["tsamp_s"] = 1e-3
                fh.attrs["fch1_mhz"] = 1500.0
                fh.attrs["foff_mhz"] = -1.0
                fh.attrs["tstart_mjd"] = 60000.0
                fh.create_dataset("wfall", data=np.zeros((4, 16), dtype=np.float32))

            with h5py.File(unsupported_h5, "w") as fh:
                fh.create_dataset("data", data=np.zeros((4, 4), dtype=np.float32))

            fits.PrimaryHDU(data=np.zeros((4, 4), dtype=np.float32)).writeto(unsupported_fits)

            with patch.dict("os.environ", {"FLITS_DATA_DIR": str(tmp_path)}):
                self.assertEqual(list_filterbank_files(), ["supported.h5"])

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

    def test_static_workspace_uses_analysis_tabs_and_export_panel(self) -> None:
        index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="directorySelect"', index_html)
        self.assertIn('data-analysis-tab="prepare"', index_html)
        self.assertIn('data-analysis-tab="dm"', index_html)
        self.assertIn('data-analysis-tab="fitting"', index_html)
        self.assertIn('data-analysis-tab="temporal"', index_html)
        self.assertIn('data-analysis-tab="export"', index_html)
        self.assertIn('id="dmResidualPlot"', index_html)
        self.assertIn('id="fitScatteringButton"', index_html)
        self.assertIn('id="fitGuessContent"', index_html)
        self.assertIn('id="fitGuessPlot"', index_html)
        self.assertIn('id="fittingSpectrumPlot"', index_html)
        self.assertIn('id="fittingProfilePlot"', index_html)
        self.assertIn('id="analysisTemporalPanel"', index_html)
        self.assertIn('id="spectralContent"', index_html)
        self.assertIn('id="acfPlot"', index_html)
        self.assertIn('id="temporalScalePlot"', index_html)
        self.assertIn('id="spectralPlot"', index_html)
        self.assertIn('id="spectralSegmentInput"', index_html)
        self.assertIn('id="runSpectralButton"', index_html)
        self.assertIn('id="buildExportButton"', index_html)
        self.assertIn('id="exportPreviewContent"', index_html)
        self.assertIn("collectFitComponentGuesses", app_js)
        self.assertIn("renderFitGuessPlot", app_js)
        self.assertIn("component_guesses", app_js)
        self.assertIn('id="exportPreviewThumbs"', index_html)
        self.assertIn('id="exportIncludeJson"', index_html)
        self.assertIn('id="exportIncludePlots"', index_html)
        self.assertIn('data-mode="offpulse"', index_html)
        self.assertIn('data-mode="region"', index_html)
        self.assertIn('id="clearOffpulseButton"', index_html)
        self.assertIn('id="exportSessionButton"', index_html)
        self.assertIn('id="importSessionInput"', index_html)
        self.assertIn('id="notesInput"', index_html)
        self.assertIn('id="dmMetricInput"', index_html)
        self.assertIn('id="sourceRaInput"', index_html)
        self.assertIn('id="sourceDecInput"', index_html)
        self.assertIn('id="timeScaleInput"', index_html)
        self.assertIn('id="observatoryLongitudeInput"', index_html)
        self.assertIn('id="observatoryLatitudeInput"', index_html)
        self.assertIn('id="observatoryHeightInput"', index_html)
        self.assertIn('id="acquisitionOverridesDetails"', index_html)
        self.assertIn('id="sourceContextDetails"', index_html)
        self.assertIn('id="timingMetadataDetails"', index_html)
        self.assertIn('id="updateTimingButton"', index_html)
        self.assertIn("Acquisition Overrides", index_html)
        self.assertIn("Source Context", index_html)
        self.assertIn("Timing Metadata", index_html)
        self.assertIn("Apply Timing Metadata", index_html)
        self.assertIn("Component Region", index_html)
        self.assertNotIn("Why this value?", app_js)
        self.assertIn("function measurementTooltip", app_js)
        self.assertIn("Reference TOA", app_js)
        self.assertIn("Peak-bin TOA (Topo MJD)", app_js)
        self.assertIn("Infinite-freq TOA (Bary TDB)", app_js)
        self.assertIn("toa_inf_bary_mjd_tdb", app_js)
        self.assertIn("set_timing_metadata", app_js)
        self.assertIn("Peak event-window S/N times the radiometer flux scale", app_js)
        self.assertIn("Selection and Provenance", app_js)
        self.assertIn("Clear Components", index_html)
        self.assertIn("activeAnalysisTab", app_js)
        self.assertIn("renderDirectoryOptions", app_js)
        self.assertIn("syncKnownFileSelection", app_js)
        self.assertIn("syncDmPlots", app_js)
        self.assertIn("syncFittingPlot", app_js)
        self.assertIn("renderSpectral", app_js)
        self.assertIn("renderAcfDiagnosticsSection", app_js)
        self.assertIn("renderAcfPlot", app_js)
        self.assertIn("syncAcfPlot", app_js)
        self.assertIn("function acfTooltip", app_js)
        self.assertIn("Burst Duration Guidance", app_js)
        self.assertIn("not the same as full burst duration", app_js)
        self.assertIn("function temporalTooltip", app_js)
        self.assertIn("Minimum Structure Scale Scan", app_js)
        self.assertIn("Scale Scan vs Duration", app_js)
        self.assertIn("no turnover in the tested range", app_js)
        self.assertIn("Power Spectrum", app_js)
        self.assertIn("Crossover", app_js)
        self.assertIn("Noise power spectrum", app_js)
        self.assertIn("Residual ratio", app_js)
        self.assertIn("syncSpectralPlot", app_js)
        self.assertIn("syncSpectralSegmentInput", app_js)
        self.assertIn("exportManifest", app_js)
        self.assertIn("exportPreview", app_js)
        self.assertIn("preview_export_results", app_js)
        self.assertIn("renderExportPlanner", app_js)
        self.assertIn("compute_widths", app_js)
        self.assertIn("downloadSessionSnapshot", app_js)
        self.assertIn("importSessionSnapshot", app_js)
        self.assertIn("dm_phase", app_js)
        self.assertIn("renderDmComponentSummary", app_js)
        self.assertIn("Selected Metric", app_js)
        self.assertIn("DMphase Reference", app_js)
        self.assertIn("renderDmMetricDefinition", app_js)
        self.assertIn("replaceState", app_js)
        self.assertIn("run_temporal_structure_analysis", app_js)
        self.assertIn("run_spectral_analysis", app_js)
        self.assertNotIn("time_downsample_factor", app_js)

    def test_preview_export_action_returns_preview_payload(self) -> None:
        session_id = "synthetic-export-preview-web"
        session = _synthetic_session()
        session.compute_properties()
        session.optimize_dm(center_dm=50.0, half_range=4.0, step=0.5)
        SESSIONS[session_id] = session

        payload = session_action(
            session_id,
            ActionRequest(type="preview_export_results", payload={"include": ["json", "plots"], "plot_formats": ["png"]}),
        )

        self.assertIsNone(payload["export_manifest"])
        self.assertIsNotNone(payload["export_preview"])
        self.assertEqual(
            payload["export_preview"]["selection"],
            {
                "include": ["json", "plots"],
                "plot_formats": ["png"],
                "window_formats": [],
                "window_resolutions": [],
            },
        )
        self.assertTrue(any(item["label"] == "Science JSON" for item in payload["export_preview"]["artifacts"]))
        self.assertTrue(any(item["plot_key"] == "dynamic_spectrum" for item in payload["export_preview"]["plot_previews"]))

    @patch("flits.web.app.BurstSession.from_file")
    @patch("flits.web.app.resolve_burst_path")
    def test_create_session_passes_selected_auto_mask_profile(self, mock_resolve_path: object, mock_from_file: object) -> None:
        mock_resolve_path.return_value = Path("/tmp/synthetic.fil")
        session = _synthetic_session(auto_mask_profile="thorough")
        session.config = ObservationConfig.from_preset(
            dm=0.0,
            preset_key="generic",
            sefd_jy=10.0,
            auto_mask_profile="thorough",
            distance_mpc=123.0,
            redshift=0.12,
            sefd_fractional_uncertainty=0.1,
            distance_fractional_uncertainty=0.2,
        )
        mock_from_file.return_value = session

        payload = create_session(
            CreateSessionRequest(
                bfile="synthetic.fil",
                dm=50.0,
                auto_mask_profile="thorough",
                distance_mpc=123.0,
                sefd_fractional_uncertainty=0.1,
                distance_fractional_uncertainty=0.2,
                redshift=0.12,
            )
        )

        try:
            self.assertEqual(payload["view"]["meta"]["auto_mask_profile"], "thorough")
            self.assertEqual(payload["view"]["meta"]["auto_mask_profile_label"], "Thorough")
            self.assertEqual(payload["view"]["meta"]["sefd_fractional_uncertainty"], 0.1)
            self.assertEqual(payload["view"]["meta"]["distance_fractional_uncertainty"], 0.2)
            mock_from_file.assert_called_once_with(
                "/tmp/synthetic.fil",
                dm=50.0,
                telescope=None,
                sefd_jy=None,
                sefd_fractional_uncertainty=0.1,
                npol_override=None,
                read_start_sec=None,
                read_end_sec=None,
                auto_mask_profile="thorough",
                distance_mpc=123.0,
                distance_fractional_uncertainty=0.2,
                redshift=0.12,
                source_ra_deg=None,
                source_dec_deg=None,
                time_scale=None,
                observatory_longitude_deg=None,
                observatory_latitude_deg=None,
                observatory_height_m=None,
            )
        finally:
            SESSIONS.pop(payload["session_id"], None)

    @patch("flits.web.app.BurstSession.from_file")
    @patch("flits.web.app.resolve_burst_path")
    def test_create_session_passes_npol_override(self, mock_resolve_path: object, mock_from_file: object) -> None:
        mock_resolve_path.return_value = Path("/tmp/synthetic.fil")
        session = _synthetic_session()
        session.config = ObservationConfig.from_preset(dm=0.0, preset_key="generic", sefd_jy=10.0, npol_override=1)
        mock_from_file.return_value = session

        payload = create_session(
            CreateSessionRequest(
                bfile="synthetic.fil",
                dm=50.0,
                npol_override=1,
            )
        )

        try:
            self.assertEqual(payload["view"]["meta"]["npol"], 1)
            self.assertEqual(payload["view"]["meta"]["npol_override"], 1)
            self.assertEqual(payload["view"]["meta"]["header_npol"], 1)
            mock_from_file.assert_called_once_with(
                "/tmp/synthetic.fil",
                dm=50.0,
                telescope=None,
                sefd_jy=None,
                sefd_fractional_uncertainty=None,
                npol_override=1,
                read_start_sec=None,
                read_end_sec=None,
                auto_mask_profile="auto",
                distance_mpc=None,
                distance_fractional_uncertainty=None,
                redshift=None,
                source_ra_deg=None,
                source_dec_deg=None,
                time_scale=None,
                observatory_longitude_deg=None,
                observatory_latitude_deg=None,
                observatory_height_m=None,
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
                    payload={"center_dm": 50.0, "half_range": 4.0, "step": 0.5, "metric": "dm_phase"},
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
        self.assertEqual(optimization["snr_metric"], "dm_phase")
        self.assertIn("applied_dm", optimization)
        self.assertIn("subband_freqs_mhz", optimization)
        self.assertIn("arrival_times_applied_ms", optimization)
        self.assertIn("arrival_times_best_ms", optimization)
        self.assertIn("residuals_applied_ms", optimization)
        self.assertIn("residuals_best_ms", optimization)
        self.assertIn("residual_status", optimization)
        self.assertIn("residual_rms_applied_ms", optimization)
        self.assertIn("residual_rms_best_ms", optimization)
        self.assertIn("component_results", optimization)
        metric_defs = payload["view"]["meta"]["dm_metrics"]
        self.assertTrue(metric_defs)
        self.assertIn("formula", metric_defs[0])
        self.assertIn("origin", metric_defs[0])
        self.assertIn("references", metric_defs[0])
        self.assertEqual([item["key"] for item in metric_defs], ["integrated_event_snr", "dm_phase"])

    def test_session_action_optimize_dm_rejects_removed_metric(self) -> None:
        session_id = "synthetic-dm-invalid-metric"
        SESSIONS[session_id] = _synthetic_session()
        try:
            with self.assertRaises(HTTPException) as context:
                session_action(
                    session_id,
                    ActionRequest(
                        type="optimize_dm",
                        payload={"center_dm": 50.0, "half_range": 4.0, "step": 0.5, "metric": "peak_snr"},
                    ),
                )
        finally:
            SESSIONS.pop(session_id, None)

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("Unsupported DM metric", context.exception.detail)

    def test_session_action_fit_scattering_dispatches_to_session_method(self) -> None:
        session_id = "synthetic-fit-dispatch"
        session = _synthetic_session()
        session.fit_scattering = Mock(return_value=None)
        payload = {
            "num_components": 1,
            "component_guesses": [
                {
                    "arrival_time_ms": 120.0,
                    "width_ms": 2.0,
                    "tau_ms": 1.0,
                    "log_amplitude": 0.5,
                }
            ],
        }
        SESSIONS[session_id] = session
        try:
            session_action(
                session_id,
                ActionRequest(type="fit_scattering", payload=payload),
            )
        finally:
            SESSIONS.pop(session_id, None)

        session.fit_scattering.assert_called_once_with(payload)

    def test_session_action_compute_properties_returns_uncertainty_details(self) -> None:
        session_id = "synthetic-compute-uncertainty"
        session = _synthetic_session()
        SESSIONS[session_id] = session
        try:
            payload = session_action(
                session_id,
                ActionRequest(type="compute_properties", payload={}),
            )
        finally:
            SESSIONS.pop(session_id, None)

        results = payload["view"]["results"]
        self.assertIsNotNone(results)
        details = results["uncertainty_details"]
        self.assertIn("toa_peak_topo_mjd", details)
        self.assertIn("toa_topo_mjd", details)
        self.assertIn("width_ms_acf", details)
        self.assertIn("fluence_jyms", details)
        self.assertIsNone(results["uncertainties"]["toa_peak_topo_mjd"])
        self.assertIsNone(results["uncertainties"]["toa_topo_mjd"])
        self.assertIsNone(results["uncertainties"]["width_ms_acf"])
        self.assertEqual(details["toa_peak_topo_mjd"]["classification"], "resolution_limit")
        self.assertEqual(details["toa_topo_mjd"]["classification"], "resolution_limit")
        self.assertEqual(details["width_ms_acf"]["classification"], "resolution_limit")
        self.assertEqual(details["fluence_jyms"]["classification"], "statistical_only")

    def test_session_action_updates_timing_metadata(self) -> None:
        session_id = "synthetic-timing-metadata"
        session = _synthetic_session()
        SESSIONS[session_id] = session
        try:
            payload = session_action(
                session_id,
                ActionRequest(
                    type="set_timing_metadata",
                    payload={
                        "source_ra_deg": 123.4,
                        "source_dec_deg": -45.6,
                        "time_scale": "tt",
                        "observatory_longitude_deg": 1.2,
                        "observatory_latitude_deg": 52.3,
                        "observatory_height_m": 10.0,
                    },
                ),
            )
        finally:
            SESSIONS.pop(session_id, None)

        meta = payload["view"]["meta"]
        self.assertEqual(meta["source_ra_deg"], 123.4)
        self.assertEqual(meta["source_dec_deg"], -45.6)
        self.assertEqual(meta["time_scale"], "tt")
        self.assertEqual(meta["observatory_longitude_deg"], 1.2)
        self.assertEqual(meta["observatory_latitude_deg"], 52.3)
        self.assertEqual(meta["observatory_height_m"], 10.0)

    def test_session_action_run_spectral_analysis_returns_serialized_payload(self) -> None:
        session_id = "synthetic-spectral-dispatch"
        session = _synthetic_session()
        expected = SpectralAnalysisResult(
            status="ok",
            message=None,
            segment_length_ms=16.0,
            segment_bins=16,
            segment_count=2,
            normalization="none",
            event_window_ms=[112.0, 128.0],
            spectral_extent_mhz=[1000.0, 1100.0],
            tsamp_ms=1.0,
            frequency_resolution_hz=62.5,
            nyquist_hz=500.0,
            freq_hz=np.array([62.5, 125.0], dtype=float),
            power=np.array([1.5, 0.75], dtype=float),
            crossover_frequency_hz=92.0,
            crossover_frequency_status="ok",
            crossover_frequency_hz_3sigma_low=80.0,
            crossover_frequency_hz_3sigma_high=105.0,
            noise_psd_freq_hz=np.array([62.5, 125.0], dtype=float),
            noise_psd_power=np.array([0.2, 0.25], dtype=float),
            noise_psd_segment_count=3,
            uncertainty_details={
                "power_law_alpha": UncertaintyDetail(
                    value=0.25,
                    units="index",
                    classification="diagnostic_only",
                    is_formal_1sigma=False,
                    publishable=False,
                    basis="web api spectral test",
                    tooltip="diagnostic spectral uncertainty",
                ),
            },
        )

        def _run(segment_length_ms: float) -> SpectralAnalysisResult:
            session.spectral_analysis = expected
            return expected

        session.run_spectral_analysis = Mock(side_effect=_run)
        SESSIONS[session_id] = session
        try:
            payload = session_action(
                session_id,
                ActionRequest(type="run_spectral_analysis", payload={"segment_length_ms": 16.0}),
            )
        finally:
            SESSIONS.pop(session_id, None)

        session.run_spectral_analysis.assert_called_once_with(segment_length_ms=16.0)
        analysis = payload["view"]["spectral_analysis"]
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis["status"], "ok")
        self.assertEqual(analysis["normalization"], "none")
        self.assertEqual(analysis["segment_length_ms"], 16.0)
        self.assertEqual(analysis["segment_bins"], 16)
        self.assertEqual(analysis["segment_count"], 2)
        self.assertEqual(analysis["freq_hz"], [62.5, 125.0])
        self.assertEqual(analysis["power"], [1.5, 0.75])
        self.assertEqual(analysis["crossover_frequency_hz"], 92.0)
        self.assertEqual(analysis["crossover_frequency_status"], "ok")
        self.assertEqual(analysis["noise_psd_power"], [0.2, 0.25])
        self.assertEqual(analysis["noise_psd_segment_count"], 3)
        self.assertEqual(analysis["uncertainty_details"]["power_law_alpha"]["classification"], "diagnostic_only")

    def test_session_action_run_temporal_structure_analysis_returns_serialized_payload(self) -> None:
        session_id = "synthetic-temporal-dispatch"
        session = _synthetic_session()
        expected = TemporalStructureResult(
            status="ok",
            message=None,
            segment_length_ms=16.0,
            segment_bins=16,
            segment_count=2,
            normalization="none",
            event_window_ms=[112.0, 128.0],
            spectral_extent_mhz=[1000.0, 1100.0],
            tsamp_ms=1.0,
            frequency_resolution_hz=62.5,
            nyquist_hz=500.0,
            min_structure_ms_primary=2.0,
            min_structure_ms_wavelet=4.0,
            fitburst_min_component_ms=3.0,
            power_law_fit_status="underconstrained",
            power_law_fit_message="Need at least 12 bins.",
            raw_periodogram_freq_hz=np.array([62.5, 125.0], dtype=float),
            raw_periodogram_power=np.array([1.8, 0.9], dtype=float),
            averaged_psd_freq_hz=np.array([62.5, 125.0], dtype=float),
            averaged_psd_power=np.array([1.5, 0.75], dtype=float),
            matched_filter_scales_ms=np.array([1.0, 2.0, 4.0], dtype=float),
            matched_filter_boxcar_sigma=np.array([2.5, 5.7, 4.1], dtype=float),
            matched_filter_gaussian_sigma=np.array([2.0, 5.1, 3.8], dtype=float),
            matched_filter_threshold_sigma=5.0,
            wavelet_scales_ms=np.array([1.0, 2.0, 4.0], dtype=float),
            wavelet_sigma=np.array([1.8, 4.9, 3.0], dtype=float),
            wavelet_threshold_sigma=5.0,
            crossover_frequency_hz=92.0,
            crossover_frequency_status="out_of_band",
            crossover_frequency_hz_3sigma_low=80.0,
            crossover_frequency_hz_3sigma_high=105.0,
            noise_psd_freq_hz=np.array([62.5, 125.0], dtype=float),
            noise_psd_power=np.array([0.2, 0.25], dtype=float),
            noise_psd_segment_count=3,
            uncertainty_details={
                "crossover_frequency_hz": UncertaintyDetail(
                    value=12.0,
                    units="Hz",
                    classification="diagnostic_only",
                    is_formal_1sigma=False,
                    publishable=False,
                    basis="web api temporal test",
                    tooltip="diagnostic crossover uncertainty",
                ),
            },
        )

        def _run(segment_length_ms: float) -> TemporalStructureResult:
            session.temporal_structure = expected
            return expected

        session.run_temporal_structure_analysis = Mock(side_effect=_run)
        SESSIONS[session_id] = session
        try:
            payload = session_action(
                session_id,
                ActionRequest(type="run_temporal_structure_analysis", payload={"segment_length_ms": 16.0}),
            )
        finally:
            SESSIONS.pop(session_id, None)

        session.run_temporal_structure_analysis.assert_called_once_with(segment_length_ms=16.0)
        temporal = payload["view"]["temporal_structure"]
        self.assertIsNotNone(temporal)
        self.assertEqual(temporal["status"], "ok")
        self.assertEqual(temporal["segment_length_ms"], 16.0)
        self.assertEqual(temporal["min_structure_ms_primary"], 2.0)
        self.assertEqual(temporal["min_structure_ms_wavelet"], 4.0)
        self.assertEqual(temporal["fitburst_min_component_ms"], 3.0)
        self.assertEqual(temporal["raw_periodogram_freq_hz"], [62.5, 125.0])
        self.assertEqual(temporal["averaged_psd_power"], [1.5, 0.75])
        self.assertEqual(temporal["matched_filter_scales_ms"], [1.0, 2.0, 4.0])
        self.assertEqual(temporal["power_law_fit_status"], "underconstrained")
        self.assertEqual(temporal["crossover_frequency_status"], "out_of_band")
        self.assertEqual(temporal["noise_psd_freq_hz"], [62.5, 125.0])
        self.assertEqual(temporal["noise_psd_segment_count"], 3)
        self.assertEqual(temporal["uncertainty_details"]["crossover_frequency_hz"]["classification"], "diagnostic_only")

    def test_session_action_supports_offpulse_width_and_notes_actions(self) -> None:
        session_id = "synthetic-phase1-actions"
        session = _synthetic_session()
        session.add_offpulse_ms = Mock()
        session.clear_offpulse = Mock()
        session.compute_widths = Mock()
        session.accept_width_result = Mock()
        session.set_notes = Mock()
        SESSIONS[session_id] = session
        try:
            session_action(session_id, ActionRequest(type="add_offpulse", payload={"start_ms": 1.0, "end_ms": 2.0}))
            session_action(session_id, ActionRequest(type="clear_offpulse", payload={}))
            session_action(session_id, ActionRequest(type="compute_widths", payload={}))
            session_action(session_id, ActionRequest(type="accept_width_result", payload={"method": "gaussian_fwhm"}))
            session_action(session_id, ActionRequest(type="set_notes", payload={"notes": "phase 1"}))
        finally:
            SESSIONS.pop(session_id, None)

        session.add_offpulse_ms.assert_called_once_with(1.0, 2.0)
        session.clear_offpulse.assert_called_once_with()
        session.compute_widths.assert_called_once_with()
        session.accept_width_result.assert_called_once_with("gaussian_fwhm")
        session.set_notes.assert_called_once_with("phase 1")

    def test_session_snapshot_endpoint_returns_portable_json(self) -> None:
        session_id = "synthetic-snapshot"
        session = _synthetic_session()
        SESSIONS[session_id] = session
        try:
            response = session_snapshot(session_id)
        finally:
            SESSIONS.pop(session_id, None)

        payload = json.loads(response.body.decode("utf-8"))
        self.assertIn("schema_version", payload)
        self.assertIn("source", payload)
        self.assertIn("crop_bins", payload)
        self.assertIn("noise_settings", payload)
        self.assertIn("width_settings", payload)

    @patch("flits.web.app.BurstSession.from_snapshot")
    def test_import_session_endpoint_creates_session_from_snapshot(self, mock_from_snapshot: object) -> None:
        mock_from_snapshot.return_value = _synthetic_session()

        payload = import_session(ImportSessionRequest(snapshot={"schema_version": "1.0", "source": {"source_path": "synthetic_dm.fil"}}))

        try:
            self.assertIn("session_id", payload)
            self.assertIn("view", payload)
            mock_from_snapshot.assert_called_once()
        finally:
            SESSIONS.pop(payload["session_id"], None)

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
        self.assertIn("toa_peak_topo_mjd", results)
        self.assertIn("toa_topo_mjd", results)
        self.assertIn("toa_inf_topo_mjd", results)
        self.assertIn("toa_inf_bary_mjd_tdb", results)
        self.assertIn("toa_status", results)
        self.assertIn("measurement_flags", results)
        self.assertIn("uncertainties", results)
        self.assertIn("provenance", results)
        self.assertIn("diagnostics", results)
        self.assertIn("mjd_at_peak", results)
        diagnostics = results["diagnostics"]
        self.assertGreater(len(diagnostics["temporal_acf"]), 0)
        self.assertGreater(len(diagnostics["temporal_acf_lags_ms"]), 0)
        self.assertGreater(len(diagnostics["spectral_acf"]), 0)
        self.assertGreater(len(diagnostics["spectral_acf_lags_mhz"]), 0)
        self.assertIn("spectral_analysis", payload["view"])
        self.assertIn("temporal_structure", payload["view"])
        self.assertIsNone(payload["view"]["spectral_analysis"])
        self.assertIsNone(payload["view"]["temporal_structure"])

    def test_delete_session_removes_session(self) -> None:
        session_id = "synthetic-delete"
        SESSIONS[session_id] = _synthetic_session()

        payload = delete_session(session_id)

        self.assertEqual(payload, {"status": "deleted"})
        self.assertNotIn(session_id, SESSIONS)


if __name__ == "__main__":
    unittest.main()
