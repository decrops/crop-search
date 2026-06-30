from __future__ import annotations

import unittest
import json
import shutil
from pathlib import Path
from unittest.mock import patch

from crop_search_framework.relationships import (
    build_relationship_matrix,
    build_relationship_query_plan,
    canonical_relationship_key,
    discover_relationships,
    load_crop_universe,
    load_relationship_vocabulary,
    modes_by_id,
    ordered_pair_key,
)
from crop_search_framework.schema_registry import SchemaRegistry


REPO_ROOT = Path(__file__).resolve().parents[1]


class RelationshipMatrixTests(unittest.TestCase):
    def test_relationship_vocabulary_is_valid(self) -> None:
        vocabulary = load_relationship_vocabulary(REPO_ROOT)
        mode_ids = {mode["mode_id"] for mode in vocabulary["modes"]}

        self.assertIn("rotation", vocabulary["default_modes"])
        self.assertIn("intercrop", mode_ids)
        self.assertIn("beneficial", vocabulary["effect_labels"])

    def test_matrix_skeleton_covers_full_current_crop_universe(self) -> None:
        matrix = build_relationship_matrix(REPO_ROOT)
        registry = SchemaRegistry(REPO_ROOT)
        registry.validate("crop-relationship-matrix.schema.json", matrix)

        crop_ids = {crop["crop_id"] for crop in matrix["crops"]}
        ordered_keys = {cell["ordered_pair_key"] for cell in matrix["cells"]}

        # Universe size is data-driven (config/crops), so derive expectations.
        n = len(list((REPO_ROOT / "config" / "crops").glob("*.json")))
        self.assertEqual(matrix["crop_count"], n)
        self.assertEqual(matrix["cell_count"], n * n)
        self.assertTrue({"corn", "cotton", "rice", "soybean", "sunflower", "tomato", "wheat"} <= crop_ids)
        self.assertIn("corn|soybean", ordered_keys)
        self.assertIn("corn|corn", ordered_keys)
        self.assertNotIn("corn|legumes", ordered_keys)
        self.assertEqual(matrix["rollups"], [])
        self.assertTrue(
            all(
                mode_status["status"] == "not_searched"
                for cell in matrix["cells"]
                for mode_status in cell["mode_statuses"].values()
            )
        )

    def test_canonical_key_preserves_order_for_rotation_and_sorts_symmetric_modes(self) -> None:
        vocabulary = load_relationship_vocabulary(REPO_ROOT)
        modes = modes_by_id(vocabulary)

        self.assertEqual(ordered_pair_key("soybean", "corn"), "soybean|corn")
        self.assertEqual(
            canonical_relationship_key(modes["rotation"], "soybean", "corn"),
            "rotation|soybean|corn",
        )
        self.assertEqual(
            canonical_relationship_key(modes["rotation"], "corn", "soybean"),
            "rotation|corn|soybean",
        )
        self.assertEqual(
            canonical_relationship_key(modes["intercrop"], "soybean", "corn"),
            "intercrop|corn|soybean",
        )
        self.assertEqual(
            canonical_relationship_key(modes["intercrop"], "corn", "soybean"),
            "intercrop|corn|soybean",
        )

    def test_rotation_query_plan_covers_all_pairs_across_source_tiers(self) -> None:
        plan = build_relationship_query_plan(REPO_ROOT, mode_ids=["rotation"], queries_per_pair=1)
        registry = SchemaRegistry(REPO_ROOT)
        registry.validate("crop-relationship-query-plan.schema.json", plan)

        n = len(list((REPO_ROOT / "config" / "crops").glob("*.json")))
        self.assertEqual(plan["crop_count"], n)
        self.assertEqual(plan["matrix_cell_count"], n * n)
        self.assertEqual(plan["planned_pair_count"], n * n)
        self.assertEqual(len(plan["source_tier_ids"]), 5)
        self.assertEqual(plan["query_count"], n * n * 5)
        self.assertFalse(plan["truncated"])

        queries = plan["queries"]
        self.assertTrue(any(q["ordered_pair_key"] == "corn|soybean" for q in queries))
        self.assertTrue(any("corn after soybean" in q["query"] for q in queries))
        self.assertTrue(any("corn continuous cropping" in q["query"] for q in queries))
        self.assertTrue(any("Zea mays" in q["query"] for q in queries if q["source_tier_id"] == "peer_reviewed_science"))
        self.assertTrue(any(q["source_tier_id"] == "extension_publication" for q in queries))

    def test_relationship_claim_schema_accepts_absent_optional_fields(self) -> None:
        claim = {
            "relationship_claim_id": "rel-1",
            "run_id": "relationship-fixture",
            "subject_crop_id": "corn",
            "object_crop_id": "soybean",
            "subject_crop_group": "cereal",
            "object_crop_group": "legume",
            "relationship_mode": "rotation",
            "relationship_subtype": "previous_crop_effect",
            "direction": "object_precedes_subject",
            "ordered_pair_key": "corn|soybean",
            "canonical_relationship_key": "rotation|corn|soybean",
            "effect": "beneficial",
            "claim_text": "Corn after soybean is reported as beneficial.",
            "evidence_text": "Corn following soybean can benefit from rotation effects.",
            "value": {},
            "context": {},
            "provenance": {
                "source_urls": ["https://example.org/rotation"],
                "source_title": "Rotation guide",
                "source_domain": "example.org",
                "document_type": "html",
                "source_tier_id": "extension_publication",
                "accessed_at": "2026-06-24T00:00:00Z",
                "extraction_method": "fixture",
            },
            "confidence": "medium",
            "status": "needs_review",
        }

        SchemaRegistry(REPO_ROOT).validate("crop-relationship-claim.schema.json", claim)

    @patch("crop_search_framework.relationships.connector_results_for_tier")
    def test_relationship_discovery_writes_pair_context_ledger(self, mock_connector) -> None:
        run_id = "unit-relationship-discovery"
        out_dir = REPO_ROOT / "exploration" / "relationships" / "discovery" / run_id
        if out_dir.exists():
            shutil.rmtree(out_dir)
        mock_connector.return_value = (
            [
                {
                    "source_url": "https://example.org/corn-rotation",
                    "source_metadata": {"doi": "10.1234/corn-rotation", "type": "journal-article"},
                    "score": 9,
                    "score_components": {"fixture": 9},
                    "access_status": "open_full_text",
                    "source_domain": "example.org",
                    "title": "Corn rotation fixture",
                    "discovery_method": "fixture_provider",
                }
            ],
            [],
        )

        try:
            summary = discover_relationships(
                REPO_ROOT,
                run_id,
                mode_ids=["rotation"],
                source_tier_ids=["extension_publication"],
                queries_per_pair=1,
                max_pairs=1,
                max_results_per_query=1,
            )
            rows = [
                json.loads(line)
                for line in (out_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(summary["queries_executed"], 1)
            self.assertEqual(summary["ledger_rows"], 1)
            self.assertEqual(summary["stage"], "relationship_discovery")
            # max_pairs=1 picks the first self-pair in the (crop_id-sorted) universe.
            first = load_crop_universe(REPO_ROOT)[0].crop_id
            self.assertEqual(rows[0]["query_kind"], "crop_relationship")
            self.assertEqual(rows[0]["ordered_pair_key"], "{0}|{0}".format(first))
            self.assertEqual(rows[0]["canonical_relationship_key"], "rotation|{0}|{0}".format(first))
            self.assertEqual(rows[0]["relationship_mode"], "rotation")
            self.assertEqual(rows[0]["source_tier"], "extension_publication")
            self.assertEqual(rows[0]["relationship_source_key"], "rotation|{0}|{0}|doi:10.1234/corn-rotation".format(first))
        finally:
            if out_dir.exists():
                shutil.rmtree(out_dir)


if __name__ == "__main__":
    unittest.main()
