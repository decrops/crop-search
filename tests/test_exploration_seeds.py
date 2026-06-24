from __future__ import annotations

import unittest
from pathlib import Path

from crop_search_framework.exploration import ExplorationRunner
from crop_search_framework.parameters import QueryPlanItem


REPO_ROOT = Path(__file__).resolve().parents[1]


def build_runner() -> ExplorationRunner:
    return ExplorationRunner(
        repo_root=REPO_ROOT,
        run_config_path=REPO_ROOT / "config" / "runs" / "pilot-global-wheat.json",
        manifest_path=REPO_ROOT / "config" / "mcp" / "servers.local.json",
        hook_config_path=REPO_ROOT / "config" / "hooks" / "default.json",
    )


class MergeResultsTests(unittest.TestCase):
    def test_search_results_take_precedence_over_seed_duplicates(self) -> None:
        search = [{"source_url": "https://a.example", "discovery_method": "openalex"}]
        seeds = [
            {"source_url": "https://a.example", "discovery_method": "source_seed"},
            {"source_url": "https://b.example", "discovery_method": "source_seed"},
        ]
        merged = ExplorationRunner._merge_results(search, seeds)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["discovery_method"], "openalex")
        self.assertEqual(merged[1]["source_url"], "https://b.example")


class GlobalWheatSeedConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = build_runner()

    def test_run_config_enables_augment_seed_mode(self) -> None:
        self.assertTrue(self.runner.run_config["use_source_seeds"])
        self.assertEqual(self.runner.run_config["seed_mode"], "augment")

    def test_seeds_cover_the_previously_scholarly_only_tiers(self) -> None:
        tiers = {s.get("source_tier_id") for s in self.runner.run_config["source_seeds"]}
        self.assertIn("extension_publication", tiers)
        self.assertIn("international_institution", tiers)
        self.assertIn("industry_grower_guide", tiers)

    def test_seed_fires_for_its_tier_and_parameter(self) -> None:
        query = QueryPlanItem(
            query="wheat growth stages maturity",
            parameter_id="phenology.maturity_duration",
            parameter_family="phenology",
            parameter_label="Maturity duration",
            source_tier_id="industry_grower_guide",
        )
        results = self.runner._seed_results_for_query(query)
        urls = {r["source_url"] for r in results}
        self.assertIn("https://ahdb.org.uk/knowledge-library/wheat-growth-guide", urls)
        self.assertTrue(all(r["discovery_method"] == "source_seed" for r in results))

    def test_seed_does_not_fire_for_wrong_tier(self) -> None:
        query = QueryPlanItem(
            query="wheat growth stages maturity",
            parameter_id="phenology.maturity_duration",
            parameter_family="phenology",
            parameter_label="Maturity duration",
            source_tier_id="peer_reviewed_science",
        )
        results = self.runner._seed_results_for_query(query)
        self.assertEqual(results, [])

    def test_seed_does_not_fire_for_unlisted_parameter(self) -> None:
        query = QueryPlanItem(
            query="wheat grain protein",
            parameter_id="quality.grain_protein",
            parameter_family="quality",
            parameter_label="Grain protein",
            source_tier_id="industry_grower_guide",
        )
        results = self.runner._seed_results_for_query(query)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
