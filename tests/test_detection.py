from __future__ import annotations

import unittest

from flits.settings import detect_preset, resolve_default_sefd_jy


class TelescopeDetectionTest(unittest.TestCase):
    def test_detects_gbt_from_telescope_id(self) -> None:
        preset_key, detection_basis = detect_preset(6, 0)

        self.assertEqual(preset_key, "gbt")
        self.assertEqual(detection_basis, "matched telescope_id=6")

    def test_detects_lofar_from_machine_id_when_telescope_is_missing(self) -> None:
        preset_key, detection_basis = detect_preset(None, 11)

        self.assertEqual(preset_key, "lofar")
        self.assertEqual(detection_basis, "matched machine_id=11")

    def test_unknown_ids_fall_back_to_generic(self) -> None:
        preset_key, detection_basis = detect_preset(999, 123)

        self.assertEqual(preset_key, "generic")
        self.assertEqual(detection_basis, "unrecognized telescope_id=999")

    def test_missing_ids_fall_back_to_generic(self) -> None:
        preset_key, detection_basis = detect_preset(None, None)

        self.assertEqual(preset_key, "generic")
        self.assertEqual(detection_basis, "no telescope_id or machine_id found")

    def test_resolves_default_gbt_l_band_sefd_from_observed_frequency_range(self) -> None:
        sefd_jy = resolve_default_sefd_jy("gbt", 1125.0, 1875.0)

        self.assertEqual(sefd_jy, 10.0)

    def test_leaves_unknown_default_sefd_unset(self) -> None:
        sefd_jy = resolve_default_sefd_jy("lofar", 110.0, 190.0)

        self.assertIsNone(sefd_jy)


if __name__ == "__main__":
    unittest.main()
