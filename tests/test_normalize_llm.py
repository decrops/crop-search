import unittest
from pathlib import Path

from crop_search_framework import normalize as nz
from crop_search_framework.normalize import NormalizationRunner, merge_extraction_claims
from crop_search_framework.parameters import load_parameter_manifest, parameter_by_id
from crop_search_framework.schema_registry import SchemaRegistry

REPO = Path(__file__).resolve().parents[1]


def run_context():
    manifest = load_parameter_manifest(REPO, "config/parameters/core-crop-parameters.json")
    return {
        "crop": "wheat",
        "region_scope": {"level": "global", "name": "global"},
        "parameter_by_id": parameter_by_id(manifest),
        "active_parameters": [p for p in manifest["parameters"]
                              if p.get("implementation_status", "active") == "active"],
        "manifest_version": manifest.get("manifest_version", ""),
    }


def a_capture():
    return {
        "run_id": "t", "source_url": "https://ex/", "final_url": "https://ex/",
        "source_title": "Wheat production guide", "source_domain": "ex",
        "document_type": "html", "accessed_at": "2026-01-01T00:00:00Z",
        "source_tier_id": "", "source_tier_label": "", "discovery_method": "",
        "access_status": "open_full_text", "publication_date_hint": "",
    }


class ClaimFromExtractionTests(unittest.TestCase):
    def setUp(self):
        self.runner = NormalizationRunner(REPO, REPO / "config/hooks/default.json")
        self.ctx = run_context()

    def test_claim_is_schema_valid_with_new_fields(self):
        extraction = {
            "parameter_id": "temperature.optimum_growth_temperature",
            "value_type": "range", "numeric_value": None,
            "range_min": 20.0, "range_max": 25.0, "unit": "celsius",
            "qualifier": "optimal",
            "evidence_text": "Optimum temperature is 20 to 25 C under irrigated wheat.",
            "claim_summary": "Optimum temperature 20-25 C (irrigated)",
            "extraction_confidence": "high",
            "cultivar": None, "management_system": "irrigated",
            "bbch_min": 30, "bbch_max": 39,
        }
        claim = self.runner._claim_from_extraction(a_capture(), extraction, self.ctx, "fixture")
        SchemaRegistry(REPO).validate("normalized-claim.schema.json", claim)
        self.assertEqual(claim["agronomic_scope"]["management_system"], "irrigated")
        self.assertEqual(claim["bbch_applicability"]["bbch_min"], 30)
        self.assertEqual(claim["bbch_applicability"]["confidence"], "high")
        self.assertEqual(claim["provenance"]["manifest_version"], self.ctx["manifest_version"])
        self.assertEqual(claim["provenance"]["extraction_method"], "llm:fixture")
        # derivable fields are NOT stored on the claim
        self.assertNotIn("domain", claim)
        self.assertNotIn("parameter_kind", claim)
        # attribute_subtype materialized from the manifest (not derived from the id)
        expected_subtype = self.ctx["parameter_by_id"][
            "temperature.optimum_growth_temperature"
        ]["normalized_attribute_subtype"]
        self.assertEqual(claim["attribute_subtype"], expected_subtype)
        self.assertEqual(claim["value"]["value_type"], "range")

    def test_optional_extraction_context_flows_to_provenance(self):
        extraction = {
            "parameter_id": "temperature.optimum_growth_temperature",
            "value_type": "range", "numeric_value": None,
            "range_min": 20.0, "range_max": 25.0, "unit": "celsius",
            "qualifier": "optimal",
            "evidence_text": "Table 2 lists optimum temperature as 20 to 25 C.",
            "claim_summary": "Optimum temperature 20-25 C",
            "extraction_confidence": "high",
            "cultivar": None, "management_system": None,
            "bbch_min": None, "bbch_max": None,
            "organisms": [{"name": "stripe rust", "role": "disease"}],
            "method": "extension guide",
            "document_id": "doc-1",
            "block_anchor": "doc-1-table-2",
            "block_type": "table",
            "page": 4,
            "table_label": "Table 2",
        }
        claim = self.runner._claim_from_extraction(a_capture(), extraction, self.ctx, "fixture")
        SchemaRegistry(REPO).validate("normalized-claim.schema.json", claim)
        provenance = claim["provenance"]
        self.assertEqual(provenance["organisms"], [{"name": "stripe rust", "role": "disease"}])
        self.assertEqual(provenance["method"], "extension guide")
        self.assertEqual(provenance["block_anchor"], "doc-1-table-2")
        self.assertEqual(provenance["block_type"], "table")

    def test_text_claim_without_stage_omits_bbch_and_agronomic(self):
        extraction = {
            "parameter_id": "management.rotation_recommendation",
            "value_type": "text", "qualifier": "recommended",
            "evidence_text": "Rotate wheat with a broadleaf crop to break disease cycles.",
            "claim_summary": "Rotate with a broadleaf crop.",
            "extraction_confidence": "medium",
            "cultivar": None, "management_system": None,
            "bbch_min": None, "bbch_max": None,
        }
        claim = self.runner._claim_from_extraction(a_capture(), extraction, self.ctx, "fixture")
        SchemaRegistry(REPO).validate("normalized-claim.schema.json", claim)
        self.assertNotIn("agronomic_scope", claim)
        self.assertNotIn("bbch_applicability", claim)
        self.assertEqual(claim["observation_type"], "recommendation")

    def test_short_evidence_dropped(self):
        extraction = {"parameter_id": "soil.ph_range", "value_type": "text",
                      "evidence_text": "ok", "claim_summary": "ok"}
        self.assertIsNone(
            self.runner._claim_from_extraction(a_capture(), extraction, self.ctx, "fixture")
        )


class MergeTests(unittest.TestCase):
    def _range_claim(self, pid, lo, hi, url, mgmt=None):
        claim = {
            "entity": {"entity_type": "crop", "name": "wheat"},
            "parameter_id": pid,
            "location_scope": {"level": "global", "name": "global"},
            "time_scope": {"label": "growing season"},
            "value": {"value_type": "range", "raw_value_text": "x",
                      "range_min": lo, "range_max": hi, "qualifier": "optimal"},
            "provenance": {"source_urls": [url]},
        }
        if mgmt:
            claim["agronomic_scope"] = {"management_system": mgmt}
        return claim

    def _text_claim(self, pid, text, url):
        return {
            "entity": {"entity_type": "crop", "name": "wheat"},
            "parameter_id": pid,
            "location_scope": {"level": "global", "name": "global"},
            "time_scope": {"label": "growing season"},
            "claim_text": text,
            "value": {"value_type": "text", "raw_value_text": text, "text_value": text},
            "provenance": {"source_urls": [url]},
        }

    def test_overlapping_numeric_merge_unions_sources(self):
        claims = [
            self._range_claim("temperature.base_temperature", 0, 4, "https://a/"),
            self._range_claim("temperature.base_temperature", 3, 6, "https://b/"),
        ]
        merged = merge_extraction_claims(claims, "run-x")
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["value"]["range_min"], 0)
        self.assertEqual(merged[0]["value"]["range_max"], 6)
        self.assertEqual(merged[0]["provenance"]["source_urls"], ["https://a/", "https://b/"])
        self.assertTrue(merged[0]["claim_id"].startswith("run-x-llm-claim-"))

    def test_different_management_system_not_merged(self):
        claims = [
            self._range_claim("nutrients.nitrogen_requirement", 100, 120, "https://a/", "irrigated"),
            self._range_claim("nutrients.nitrogen_requirement", 100, 120, "https://b/", "dryland"),
        ]
        merged = merge_extraction_claims(claims, "run-y")
        self.assertEqual(len(merged), 2)

    def test_identical_text_deduped(self):
        claims = [
            self._text_claim("management.rotation_recommendation", "Rotate with a legume.", "https://a/"),
            self._text_claim("management.rotation_recommendation", "Rotate with a legume.", "https://b/"),
        ]
        merged = merge_extraction_claims(claims, "run-z")
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["provenance"]["source_urls"], ["https://a/", "https://b/"])


class ConflictKeyTests(unittest.TestCase):
    def test_conflict_key_includes_agronomic_scope(self):
        base = {
            "entity": {"name": "wheat"}, "attribute": "x", "attribute_subtype": "y",
            "location_scope": {"level": "global", "name": "global"},
            "time_scope": {"label": "growing season"},
        }
        with_sys = dict(base, agronomic_scope={"management_system": "irrigated"})
        self.assertNotEqual(nz.conflict_group_key(base), nz.conflict_group_key(with_sys))


if __name__ == "__main__":
    unittest.main()
