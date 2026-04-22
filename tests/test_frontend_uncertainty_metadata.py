from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
