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

    def test_dm_input_starts_blank_and_explains_manual_entry(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")

        self.assertIn('id="dmInput" type="number" step="0.001" placeholder="enter DM or 0 if already dedispersed"', html)
        self.assertNotIn('value="527.851"', html)
        self.assertIn("Some formats provide an automatic suggestion.", html)

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

    def test_dm_suggestion_helper_clears_unsuggested_values(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertRegex(
            source,
            re.compile(
                r"function maybeApplySuggestedDm\(payload, bfile\).*const suggestedDm = parseOptionalNumber\(payload\?\.suggested_dm\).*setDmInputValue\(\"\"\).*setDmInputValue\(suggestedDm\)",
                re.S,
            ),
        )

    def test_detection_hint_and_actions_require_explicit_dm(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertIn("FLITS did not detect a DM from the file metadata.", source)
        self.assertIn("use 0 for already-dedispersed input", source)
        self.assertIn("function requireDmValue(actionLabel, options = {})", source)
        self.assertIn("Enter a DM value or explicit 0 before", source)
        self.assertIn("const hasRequiredDm = hasRequiredDmValue()", source)
        self.assertIn("loadButton.disabled = isBusy || !hasBurstPath || !hasRequiredDm", source)
        self.assertIn("setDmButton.disabled = !hasSession || isBusy || !hasRequiredDm", source)
        self.assertIn("optimizeDmButton.disabled = !hasSession || isBusy || !hasRequiredDm", source)
        self.assertNotIn("dm: Number(dmInput.value)", source)
        self.assertNotIn("center_dm: Number(dmInput.value)", source)


if __name__ == "__main__":
    unittest.main()
