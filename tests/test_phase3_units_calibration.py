from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from crop_search_framework import unit_normalize as un
from crop_search_framework import calibration as cal

REPO = Path(__file__).resolve().parents[1]


class CanonicalizeValueTests(unittest.TestCase):
    def test_fahrenheit_converted_to_celsius(self):
        v, notes = un.canonicalize_value(
            "temperature.soil_emergence_temperature",
            {"value_type": "numeric", "raw_value_text": "37 F", "numeric_value": 37.0, "unit": "°F"},
        )
        self.assertEqual(v["unit"], "celsius")
        self.assertAlmostEqual(v["numeric_value"], 2.78, places=1)
        self.assertEqual(notes[0]["kind"], "converted")

    def test_seeding_rate_outlier_flagged_not_converted(self):
        v, notes = un.canonicalize_value(
            "planting.seeding_rate",
            {"value_type": "numeric", "raw_value_text": "x", "numeric_value": 1350000.0, "unit": "kg/ha"},
        )
        self.assertEqual(v["numeric_value"], 1350000.0)  # unchanged
        self.assertTrue(any(n["kind"] == "flag" for n in notes))

    def test_clean_value_untouched(self):
        v, notes = un.canonicalize_value(
            "nutrients.nitrogen_requirement",
            {"value_type": "numeric", "raw_value_text": "x", "numeric_value": 120.0, "unit": "kg/ha"},
        )
        self.assertEqual(notes, [])
        self.assertEqual(v["unit"], "kg/ha")


class NormalizeRunTests(unittest.TestCase):
    def test_run_writes_clean_valid_claims_and_report(self):
        # Use 3 real schema-valid normalized claims from the live wheat-002 run.
        src = REPO / "exploration/normalized/pilot-global-wheat-002"
        picks = [p for p in sorted(src.glob("*.json"))[:6] if p.name != "summary.json"][:3]
        if not picks:
            self.skipTest("no live normalized claims available")
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            shutil.copytree(REPO / "schemas", tmp / "schemas")
            d = tmp / "exploration/normalized/run-x"; d.mkdir(parents=True)
            for p in picks:
                shutil.copy(p, d / p.name)
            summary = un.normalize_units_run(tmp, "run-x")
            self.assertEqual(summary["claims_written"], len(picks))
            self.assertTrue((tmp / "exploration/normalized_units/run-x/units_report.json").exists())


class CalibrationTests(unittest.TestCase):
    def _repo(self, tmp: Path):
        d = tmp / "exploration/normalized/run-x"; d.mkdir(parents=True)
        claims = [
            {"claim_id": "c1", "parameter_id": "soil.ph_range",
             "value": {"value_type": "range", "raw_value_text": "x", "range_min": 6.0, "range_max": 8.0, "unit": "pH"}},
            {"claim_id": "c2", "parameter_id": "nutrients.nitrogen_requirement",
             "value": {"value_type": "numeric", "raw_value_text": "x", "numeric_value": 120.0, "unit": "kg/ha"}},
        ]
        for c in claims:
            (d / (c["claim_id"] + ".json")).write_text(json.dumps(c), encoding="utf-8")

    def test_note_has_marker_and_sections(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); self._repo(tmp)
            note = cal.build_calibration_note(tmp, "run-x", "wheat", "Freiburg", generated_at="2026-06-24")
        self.assertIn("generated_by: crop-search", note)
        self.assertIn("Local calibration (Freiburg)", note)
        self.assertIn("From a soil test", note)
        self.assertIn("6.0–8.0 pH", note)
        self.assertIn("[[Wheat — Nitrogen requirement]]", note)

    def test_render_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); self._repo(tmp)
            vault = tmp / "vault"
            res = cal.render_local_calibration(tmp, "run-x", "wheat", "Freiburg", vault, dry_run=True)
            self.assertTrue(res["dry_run"])
            self.assertFalse((vault / "DeCropsResearch/crop_science").exists())

    def test_render_writes_marker_guarded(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); self._repo(tmp)
            vault = tmp / "vault"
            res = cal.render_local_calibration(tmp, "run-x", "wheat", "Freiburg", vault, dry_run=False)
            self.assertTrue(res.get("written"))
            target = Path(res["file"])
            self.assertTrue(target.exists())
            # a foreign (unmarked) file is not overwritten
            target.write_text("hand-written note, no marker", encoding="utf-8")
            res2 = cal.render_local_calibration(tmp, "run-x", "wheat", "Freiburg", vault, dry_run=False)
            self.assertTrue(res2.get("skipped_foreign"))


if __name__ == "__main__":
    unittest.main()
