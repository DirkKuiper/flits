from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "flits" / "web_static" / "app.js"
INDEX_HTML = ROOT / "flits" / "web_static" / "index.html"


class FrontendUncertaintyMetadataTest(unittest.TestCase):
    def test_frontend_reads_backend_uncertainty_metadata(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertIn("function uncertaintyBadgeMarkup(detail)", source)
        self.assertIn("function uncertaintyTooltip(detail)", source)
        self.assertIn("function resultUncertaintyDetail(results, key, units)", source)
        self.assertIn("function findAcceptedWidthDetail(acceptedWidth, widthAnalysis, results, hasAcfFallback)", source)
        self.assertIn("uncertainty_details", source)
        self.assertIn("legacy_scalar_uncertainty", source)
        self.assertIn("detail: resultUncertaintyDetail(", source)
        self.assertIn("detail.value === null", source)

    def test_load_form_exposes_publication_systematic_inputs(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")

        self.assertIn('id="sefdFractionalUncertaintyInput"', html)
        self.assertIn('id="distanceFractionalUncertaintyInput"', html)

    def test_load_form_uses_collapsible_optional_sections(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")

        self.assertIn('id="acquisitionOverridesDetails"', html)
        self.assertIn('id="sourceContextDetails"', html)
        self.assertIn('id="timingMetadataDetails"', html)
        self.assertIn("Acquisition Overrides", html)
        self.assertIn("Source Context", html)
        self.assertIn("Timing Metadata", html)
        self.assertIn("Apply Timing Metadata", html)

    def test_prepare_view_uses_reference_toa_headline_card(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertIn('renderMeasurementCard("Reference TOA"', source)
        self.assertNotIn('renderMeasurementCard("Peak-bin TOA (Topo MJD)"', source)
        self.assertIn('resultTile("Peak-bin TOA (Topo MJD)"', source)
        self.assertIn("Timing Details", source)
        self.assertIn("Signal and Selection", source)

    def test_reference_toa_helper_prefers_bary_then_topo_then_peak(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertRegex(
            source,
            re.compile(
                r"function referenceToaCardData\(results\).*toa_inf_bary_mjd_tdb.*toa_inf_topo_mjd.*toa_peak_topo_mjd",
                re.S,
            ),
        )


if __name__ == "__main__":
    unittest.main()
