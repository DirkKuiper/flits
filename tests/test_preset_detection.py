from __future__ import annotations

import unittest

from flits.settings import detect_preset


class DetectPresetCascadeTest(unittest.TestCase):
    def test_telescope_id_wins_over_other_hints(self) -> None:
        # telescope_id=6 is GBT; other hints would point at CHIME if consulted.
        preset_key, basis = detect_preset(
            6,
            None,
            telescope_name="CHIME",
            schema_version="chime_frb_catalog_v1",
            freq_lo_mhz=400.0,
            freq_hi_mhz=800.0,
        )

        self.assertEqual(preset_key, "gbt")
        self.assertEqual(basis, "matched telescope_id=6")

    def test_schema_signature_wins_over_name_and_band(self) -> None:
        preset_key, basis = detect_preset(
            None,
            None,
            telescope_name="not-a-telescope",
            schema_version="chime_frb_catalog_v1",
            freq_lo_mhz=400.0,
            freq_hi_mhz=800.0,
        )

        self.assertEqual(preset_key, "chime")
        self.assertIn("chime_frb_catalog_v1", basis)

    def test_telescope_name_alias_matches_chime(self) -> None:
        # Normalization strips whitespace/punctuation.
        preset_key, basis = detect_preset(
            None,
            None,
            telescope_name="CHIME/FRB",
        )

        self.assertEqual(preset_key, "chime")
        self.assertIn("CHIME/FRB", basis)

    def test_telescope_name_alias_wins_over_machine_id(self) -> None:
        preset_key, _ = detect_preset(
            None,
            11,  # lofar machine_id
            telescope_name="CHIME",
        )

        self.assertEqual(preset_key, "chime")

    def test_machine_id_used_when_no_stronger_hints(self) -> None:
        preset_key, basis = detect_preset(None, 11)

        self.assertEqual(preset_key, "lofar")
        self.assertEqual(basis, "matched machine_id=11")

    def test_frequency_band_only_does_not_claim_chime(self) -> None:
        preset_key, basis = detect_preset(
            None,
            None,
            freq_lo_mhz=410.0,
            freq_hi_mhz=790.0,
        )

        self.assertEqual(preset_key, "generic")
        self.assertEqual(basis, "no matching telescope hints")

    def test_frequency_band_outside_any_preset_is_generic(self) -> None:
        preset_key, basis = detect_preset(
            None,
            None,
            freq_lo_mhz=1200.0,
            freq_hi_mhz=1800.0,
        )

        self.assertEqual(preset_key, "generic")
        self.assertEqual(basis, "no matching telescope hints")

    def test_no_hints_at_all_returns_generic(self) -> None:
        preset_key, basis = detect_preset(None, None)

        self.assertEqual(preset_key, "generic")
        self.assertEqual(basis, "no matching telescope hints")

    def test_unrecognized_telescope_id_falls_through_and_reports_it(self) -> None:
        preset_key, basis = detect_preset(999, None)

        self.assertEqual(preset_key, "generic")
        self.assertEqual(basis, "unrecognized telescope_id=999")

    def test_unknown_schema_string_does_not_claim_by_band(self) -> None:
        preset_key, _ = detect_preset(
            None,
            None,
            schema_version="something_random",
            freq_lo_mhz=450.0,
            freq_hi_mhz=750.0,
        )

        self.assertEqual(preset_key, "generic")


if __name__ == "__main__":
    unittest.main()
