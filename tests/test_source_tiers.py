from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from crop_search_framework.coverage import (
    build_source_tier_summary,
    parameter_source_tier_counts,
    science_textbook_status,
)
from crop_search_framework.dev_tools.discovery_connectors import open_library_results, openalex_results
from crop_search_framework.parameters import query_plan_for_run
from crop_search_framework.quality import score_source_result
from crop_search_framework.schema_registry import SchemaRegistry
from crop_search_framework.source_tiers import load_source_tier_manifest


REPO_ROOT = Path(__file__).resolve().parents[1]


class SourceTierTests(unittest.TestCase):
    def test_source_tier_manifest_is_valid(self) -> None:
        manifest = load_source_tier_manifest(REPO_ROOT, "config/source-tiers/default.json")
        self.assertEqual(manifest["default_policy_id"], "comprehensive_accessible")
        self.assertEqual(len(manifest["tiers"]), 5)

    def test_global_query_plan_targets_all_accessible_tiers_without_us_scope(self) -> None:
        run_config_path = REPO_ROOT / "config" / "runs" / "pilot-global-wheat.json"
        with run_config_path.open("r", encoding="utf-8") as handle:
            run_config = json.load(handle)
        SchemaRegistry(REPO_ROOT).validate("exploration-run.schema.json", run_config)
        plan = query_plan_for_run(REPO_ROOT, run_config)
        tier_ids = {item.source_tier_id for item in plan}
        parameter_ids = {item.parameter_id for item in plan}
        parameter_families = {item.parameter_family for item in plan}

        # Distinct params in the plan = active params applicable to this crop group
        # (computed, so activating new params doesn't require editing a magic number).
        from crop_search_framework.parameters import (
            load_parameter_manifest, load_crop_profile, selected_parameters,
        )
        manifest = load_parameter_manifest(REPO_ROOT, run_config["parameter_manifest_path"])
        crop_profile = load_crop_profile(REPO_ROOT, run_config["crop_profile_path"])
        expected = {p["parameter_id"] for p in selected_parameters(run_config, manifest, crop_profile)}
        self.assertEqual(parameter_ids, expected)
        self.assertGreaterEqual(len(parameter_ids), 85)  # >=85 after crop-protection activation
        self.assertEqual(len(plan), len(parameter_ids) * len(tier_ids))
        self.assertEqual(
            tier_ids,
            {
                "peer_reviewed_science",
                "textbook_reference",
                "international_institution",
                "extension_publication",
                "industry_grower_guide",
            },
        )
        self.assertIn("canopy", parameter_families)
        self.assertIn("photosynthesis", parameter_families)
        self.assertIn("root", parameter_families)
        self.assertTrue(any("peer reviewed" in item.query for item in plan))
        self.assertTrue(any("FAO" in item.query for item in plan))
        self.assertFalse(any("United States" in item.query for item in plan))

    def test_source_tier_scoring_boosts_peer_reviewed_and_institutional_sources(self) -> None:
        baseline = score_source_result(
            "wheat base temperature scientific publication peer reviewed",
            "Wheat base temperature overview",
            "Crop physiology reference.",
            "example.com",
            "https://example.com/wheat",
            "wheat",
        )
        peer_reviewed = score_source_result(
            "wheat base temperature scientific publication peer reviewed",
            "Journal article DOI wheat base temperature response",
            "Field experiment in crop physiology.",
            "doi.org",
            "https://doi.org/10.1000/example",
            "wheat",
        )
        institutional = score_source_result(
            "rice evapotranspiration FAO CGIAR crop guide",
            "FAO rice crop water requirements production manual",
            "International crop guide.",
            "fao.org",
            "https://www.fao.org/example",
            "rice",
        )

        self.assertGreater(peer_reviewed, baseline)
        self.assertGreater(institutional, baseline)

    @patch("crop_search_framework.dev_tools.discovery_connectors._get_json")
    def test_openalex_connector_marks_open_text_and_metadata_only(self, mock_get) -> None:
        mock_get.return_value = FakeResponse(
            {
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "display_name": "Wheat base temperature response",
                        "doi": "https://doi.org/10.1000/wheat",
                        "publication_year": 2024,
                        "open_access": {
                            "is_oa": True,
                            "oa_url": "https://example.org/wheat-open.pdf",
                            "oa_status": "gold",
                        },
                        "primary_location": {"source": {"display_name": "Crop Physiology Journal"}},
                    },
                    {
                        "id": "https://openalex.org/W2",
                        "display_name": "Wheat germination temperature",
                        "doi": "https://doi.org/10.1000/closed",
                        "publication_year": 2021,
                        "open_access": {"is_oa": False},
                        "primary_location": {"source": {"display_name": "Agronomy Journal"}},
                    },
                ]
            }
        )

        results = openalex_results("wheat base temperature", "wheat", 2, "test-agent")

        self.assertEqual(results[0]["access_status"], "open_full_text")
        self.assertEqual(results[0]["source_url"], "https://example.org/wheat-open.pdf")
        self.assertEqual(results[1]["access_status"], "metadata_only")
        self.assertEqual(results[1]["discovery_method"], "openalex")

    @patch("crop_search_framework.dev_tools.discovery_connectors._get_json")
    def test_open_library_connector_prefers_archive_sources_when_available(self, mock_get) -> None:
        mock_get.return_value = FakeResponse(
            {
                "docs": [
                    {
                        "key": "/works/OL1W",
                        "title": "Crop Physiology Textbook",
                        "author_name": ["A. Author"],
                        "first_publish_year": 1998,
                        "ia": ["cropphysiology00auth"],
                    }
                ]
            }
        )

        results = open_library_results("crop physiology textbook wheat", "wheat", 1, "test-agent")

        self.assertEqual(results[0]["access_status"], "open_full_text")
        self.assertEqual(results[0]["source_domain"], "archive.org")
        self.assertEqual(results[0]["discovery_method"], "open_library")

    def test_parameter_source_tier_counts_exposes_science_textbook_status(self) -> None:
        tier_counts = parameter_source_tier_counts(
            "temperature.base_temperature",
            {
                "peer_reviewed_science": "Peer-Reviewed Science",
                "textbook_reference": "Textbooks and Reference Books",
                "extension_publication": "Extension and Public Agronomy Guides",
            },
            Counter({
                ("temperature.base_temperature", "peer_reviewed_science"): 1,
                ("temperature.base_temperature", "textbook_reference"): 1,
                ("temperature.base_temperature", "extension_publication"): 1,
            }),
            Counter({("temperature.base_temperature", "peer_reviewed_science"): 1}),
            Counter(),
            Counter(),
        )

        self.assertEqual(science_textbook_status(tier_counts), "candidate_science_or_textbook")

    def test_source_tier_summary_counts_access_and_discovery_methods(self) -> None:
        summary = build_source_tier_summary(
            raw_summary={
                "query_summaries": [
                    {
                        "source_tier_id": "peer_reviewed_science",
                        "search_results_returned": 2,
                        "results_returned": 2,
                    }
                ]
            },
            raw_captures=[
                {
                    "source_tier_id": "peer_reviewed_science",
                    "access_status": "metadata_only",
                    "discovery_method": "crossref",
                    "candidate_claims": [],
                },
                {
                    "source_tier_id": "peer_reviewed_science",
                    "access_status": "open_full_text",
                    "discovery_method": "openalex",
                    "candidate_claims": ["The base temperature for wheat is 0 C."],
                },
            ],
            source_tier_labels={"peer_reviewed_science": "Peer-Reviewed Science"},
            query_tier_counts=Counter({"peer_reviewed_science": 1}),
            normalized_tier_counts=Counter({"peer_reviewed_science": 1}),
            promoted_tier_counts=Counter(),
            review_tier_counts=Counter({"peer_reviewed_science": 1}),
        )

        self.assertEqual(summary[0]["metadata_only_capture_count"], 1)
        self.assertEqual(summary[0]["open_full_text_capture_count"], 1)
        self.assertEqual(summary[0]["discovery_method_counts"]["openalex"], 1)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


if __name__ == "__main__":
    unittest.main()
