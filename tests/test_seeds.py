from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from crop_search_framework import seeds

REPO = Path(__file__).resolve().parents[1]


class SeedRegistryTests(unittest.TestCase):
    def test_real_wheat_registry_validates(self):
        report = seeds.validate_seeds(
            REPO, "config/seeds/wheat.json", "config/parameters/core-crop-parameters.json"
        )
        self.assertGreaterEqual(report["seed_count"], 10)  # 10 base + IPM seeds
        self.assertEqual(report["unknown_parameter_refs"], [])
        self.assertTrue(report["valid"])

    def test_seeds_for_run_expands_registry_filtered_by_selector(self):
        run_config = {
            "seed_registry_path": "config/seeds/wheat.json",
            "seed_selector": {"crop": "wheat", "source_tier_id": "international_institution"},
        }
        inline = seeds.seeds_for_run(REPO, run_config)
        self.assertTrue(inline)
        self.assertTrue(all(s["source_tier_id"] == "international_institution" for s in inline))
        # mapped into the inline shape the runner expects
        self.assertIn("parameter_ids", inline[0])
        self.assertIn("source_url", inline[0])

    def test_inline_seeds_take_precedence(self):
        run_config = {
            "source_seeds": [{"source_url": "https://x", "parameter_ids": []}],
            "seed_registry_path": "config/seeds/wheat.json",
        }
        self.assertEqual(seeds.seeds_for_run(REPO, run_config)[0]["source_url"], "https://x")

    def test_unknown_parameter_ref_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shutil.copytree(REPO / "schemas", tmp / "schemas")
            (tmp / "config/seeds").mkdir(parents=True)
            (tmp / "config/seeds/x.json").write_text(json.dumps({
                "registry_version": "0.1.0",
                "seeds": [{
                    "seed_id": "s1", "source_url": "https://x", "crop": "wheat",
                    "source_tier_id": "extension_publication",
                    "covered_parameters": ["nutrients.nitrogen_requirement", "bogus.param"],
                }],
            }), encoding="utf-8")
            (tmp / "config/parameters").mkdir(parents=True)
            (tmp / "config/parameters/m.json").write_text(json.dumps({
                "manifest_version": "0.3.0",
                "parameters": [{"parameter_id": "nutrients.nitrogen_requirement"}],
            }), encoding="utf-8")
            report = seeds.validate_seeds(tmp, "config/seeds/x.json", "config/parameters/m.json")
        self.assertFalse(report["valid"])
        self.assertEqual(report["unknown_parameter_refs"][0]["parameter_id"], "bogus.param")


if __name__ == "__main__":
    unittest.main()
