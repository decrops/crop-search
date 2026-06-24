from __future__ import annotations

import json
import unittest
from pathlib import Path

from crop_search_framework.parameters import load_parameter_manifest, query_plan_for_run

REPO = Path(__file__).resolve().parents[1]


class QueryTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run_config = json.loads((REPO / "config/runs/pilot-global-wheat.json").read_text())
        plan = query_plan_for_run(REPO, run_config)
        cls.scholarly = {
            q.parameter_id: q.query for q in plan if q.source_tier_id == "peer_reviewed_science"
        }
        cls.extension = {
            q.parameter_id: q.query for q in plan if q.source_tier_id == "extension_publication"
        }

    def test_nutrient_query_has_units_and_idiom(self):
        q = self.scholarly["nutrients.nitrogen_requirement"]
        self.assertIn("kg/ha", q)
        self.assertIn("N rate", q)

    def test_water_query_has_kc_and_stage_vocab(self):
        # water.crop_coefficient requires_stage_context -> a stage term is added.
        q = self.scholarly["water.crop_coefficient"]
        self.assertIn("Kc", q)
        self.assertIn("BBCH", q)

    def test_scholarly_tier_gets_scientific_name(self):
        self.assertIn("Triticum aestivum", self.scholarly["nutrients.nitrogen_requirement"])

    def test_extension_tier_omits_scientific_name(self):
        self.assertNotIn("Triticum aestivum", self.extension["nutrients.nitrogen_requirement"])

    def test_no_blunt_value_token_when_units_present(self):
        # The old generic "value" token is replaced by parameter-specific units.
        self.assertNotIn(" value", self.scholarly["nutrients.nitrogen_requirement"])

    def test_manifest_accepts_parameter_authoring_metadata(self):
        manifest = load_parameter_manifest(REPO, "config/parameters/core-crop-parameters.json")
        by_id = {p["parameter_id"]: p for p in manifest["parameters"]}
        nitrogen = by_id["nutrients.nitrogen_requirement"]
        self.assertEqual(nitrogen["query_units"], ["kg/ha", "lb/ac"])
        self.assertIn("expected_value_shape", nitrogen)
        self.assertEqual(nitrogen["implementation_status"], "active")


if __name__ == "__main__":
    unittest.main()
