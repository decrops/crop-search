from __future__ import annotations

import unittest

from crop_search_framework.dev_tools.parse_document import select_claims
from crop_search_framework.normalize import (
    apply_conflict_flags,
    extract_temperature_value,
    infer_attribute_metadata,
    infer_location_scope,
    infer_source_geo_scope,
    should_normalize_claim,
)
from crop_search_framework.review import review_claim


def claim(
    claim_id: str,
    attribute: str,
    attribute_subtype: str,
    value,
    domain: str,
):
    return {
        "claim_id": claim_id,
        "run_id": "test-run",
        "entity": {"entity_type": "crop", "name": "corn"},
        "attribute": attribute,
        "attribute_subtype": attribute_subtype,
        "claim_text": "The base temperature for corn development is 50 F.",
        "value": value,
        "location_scope": {"level": "global", "name": "global"},
        "source_geo_scope": {"level": "global", "name": "global"},
        "geo_evidence": {
            "claim_location_source": "default_global",
            "claim_location_confidence": "none",
            "claim_location_text": "",
            "source_location_source": "default_global",
            "source_location_confidence": "none",
            "source_location_text": "",
            "matched_locations": [],
        },
        "time_scope": {"label": "growing season"},
        "provenance": {
            "source_urls": ["https://example.com/source"],
            "source_title": "Corn Extension Guide",
            "source_domain": domain,
            "document_type": "pdf",
            "accessed_at": "2026-05-29T00:00:00Z",
            "extraction_method": "test",
            "evidence_text": "The base temperature for corn development is 50 F.",
        },
        "observation_type": "threshold",
        "confidence": "high",
        "conflict_status": "none",
        "status": "load_ready",
    }


class NormalizeReviewTests(unittest.TestCase):
    def test_temperature_subtypes_are_specific(self) -> None:
        self.assertEqual(
            infer_attribute_metadata("The base temperature for corn development is 50°F ."),
            ("temperature_requirement", "base_temperature", "threshold"),
        )
        self.assertEqual(
            infer_attribute_metadata("Corn usually begins to stress when air temperatures exceed 90 F."),
            ("temperature_requirement", "stress_temperature", "risk"),
        )
        self.assertEqual(
            infer_attribute_metadata("Corn can survive brief exposures to adverse temperatures near 32 F."),
            ("temperature_requirement", "survival_temperature", "threshold"),
        )
        self.assertEqual(
            infer_attribute_metadata("Planting corn into a soil temperature ranging from 50 to 55 degrees Fahrenheit may take 18 days to emerge."),
            ("temperature_requirement", "soil_emergence_temperature", "threshold"),
        )

    def test_compatible_base_temperature_claims_do_not_conflict(self) -> None:
        claims = [
            claim(
                "claim-001",
                "temperature_requirement",
                "base_temperature",
                {
                    "value_type": "numeric",
                    "raw_value_text": "50 F",
                    "numeric_value": 50,
                    "unit": "fahrenheit",
                    "normalized_numeric_value": 10,
                    "normalized_unit": "celsius",
                },
                "extension.example.edu",
            ),
            claim(
                "claim-002",
                "temperature_requirement",
                "base_temperature",
                {
                    "value_type": "range",
                    "raw_value_text": "40 F to 50 F",
                    "range_min": 40,
                    "range_max": 50,
                    "unit": "fahrenheit",
                    "normalized_range_min": 4.44,
                    "normalized_range_max": 10,
                    "normalized_unit": "celsius",
                },
                "agronomy.example.edu",
            ),
        ]
        self.assertEqual(apply_conflict_flags(claims), {"groups": 0, "claims": 0})
        self.assertEqual([item["conflict_status"] for item in claims], ["none", "none"])

    def test_temperature_parser_handles_trailing_range_units_and_corn_context(self) -> None:
        soil_range = extract_temperature_value(
            "Planting corn into a soil temperature ranging from 50 to 55 degrees Fahrenheit may take 18 days to emerge."
        )
        self.assertEqual(soil_range["range_min"], 50)
        self.assertEqual(soil_range["range_max"], 55)
        self.assertEqual(soil_range["normalized_range_min"], 10.0)

        corn_base = extract_temperature_value(
            "A base temperature of 40 F (5 C) is used for cool-season crops while a higher temperature of 50 F (10 C) is used for field corn."
        )
        self.assertEqual(corn_base["value_type"], "numeric")
        self.assertEqual(corn_base["numeric_value"], 50)

    def test_layout_artifacts_are_filtered_before_normalization(self) -> None:
        capture = {
            "query": "corn growing conditions Iowa extension",
            "source_title": "Corn Date of Planting and Maturity in Northeast Iowa",
            "search_title": "Corn Date of Planting and Maturity in Northeast Iowa",
            "search_snippet": "Corn growing conditions Iowa extension",
            "source_domain": "www.iastatedigitalpress.com",
            "final_url": "https://example.edu/corn.pdf",
            "source_url": "https://example.edu/corn.pdf",
        }
        artifact = "Date of planting 101-day 105-day Average H20 Yield H20 Yield % bu/ac % bu/ac"
        self.assertFalse(should_normalize_claim(artifact, capture, "corn"))

        header = "Wheat Production Handbook K-State Research & Extension Manhattan, Kansas1"
        capture["source_title"] = "Wheat Production Handbook - Kansas State University"
        capture["search_title"] = "Wheat Production Handbook - Kansas State University"
        self.assertFalse(should_normalize_claim(header, capture, "wheat"))

    def test_parser_rejects_layout_artifacts(self) -> None:
        claims = select_claims(
            [
                "Date of planting 101-day 105-day Average H20 Yield H20 Yield % bu/ac % bu/ac.",
                "Wheat Production Handbook K-State Research & Extension Manhattan, Kansas1.",
                "10 – Water Management (Updated July 2021).",
                "Sears, Wheat Breeder, Agronomy 7 Planting Practices James P.",
                "Smith, J. 2020. Wheat physiology and development. Crop Science 60:10-15.",
                "The base temperature for corn development is 50 F.",
            ],
            "Wheat Production Handbook - Kansas State University",
            "wheat temperature requirements",
            "wheat",
        )
        self.assertEqual(claims, ["The base temperature for corn development is 50 F."])

    def test_parser_preserves_real_index_claims(self) -> None:
        claims = select_claims(
            [
                "Leaf area index for wheat growth can reach 5 under irrigated conditions.",
                "Leaf area index, 17, 22, 31.",
            ],
            "Crop Physiology Textbook",
            "wheat growth conditions textbook",
            "wheat",
        )
        self.assertEqual(claims, ["Leaf area index for wheat growth can reach 5 under irrigated conditions."])

    def test_review_can_promote_clean_global_quantitative_claim(self) -> None:
        clean_claim = claim(
            "claim-001",
            "water_requirement",
            "evapotranspiration_requirement",
            {
                "value_type": "numeric",
                "raw_value_text": "22 inches",
                "numeric_value": 22,
                "unit": "inches",
            },
            "crops.extension.iastate.edu",
        )
        clusters = {
            "cluster-001": {
                "cluster_id": "cluster-001",
                "key": ("corn", "water_requirement", "evapotranspiration_requirement", "global", "global", "growing season", "numeric:22:inches"),
                "claims": [clean_claim],
            }
        }
        review = review_claim(clean_claim, "cluster-001", clusters)
        self.assertEqual(review["decision"], "canonical_candidate")
        self.assertEqual(review["attribute_subtype"], "evapotranspiration_requirement")

    def test_claim_location_scope_captures_explicit_state_names(self) -> None:
        capture = {
            "source_title": "Winter Wheat Planting Guide - SDSU Extension",
            "search_title": "",
        }
        scope = infer_location_scope(
            "Winter wheat planting in South Dakota begins in mid September.",
            capture,
            {"level": "country", "name": "United States"},
        )
        self.assertEqual(scope["level"], "state")
        self.assertEqual(scope["name"], "South Dakota")
        self.assertEqual(scope["geo_id"], "census:0400000US46")
        self.assertEqual(scope["geocode_source"], "us_census_gazetteer_2025:state")
        self.assertEqual(scope["centroid"]["lat"], 44.446796)

    def test_claim_location_scope_captures_state_qualified_counties(self) -> None:
        capture = {
            "source_title": "Iowa Crop Production Guide",
            "search_title": "",
        }
        scope = infer_location_scope(
            "Corn planting in Chickasaw County, Iowa begins when soil temperatures reach 50 F.",
            capture,
            {"level": "country", "name": "United States"},
        )
        self.assertEqual(scope["level"], "county")
        self.assertEqual(scope["name"], "Chickasaw County, Iowa")
        self.assertEqual(scope["geo_id"], "census:0500000US19037")
        self.assertEqual(scope["centroid"]["lat"], 43.059741)

    def test_source_geo_scope_captures_named_production_regions(self) -> None:
        capture = {
            "source_title": "Sunflower Production Guide for the Northern Great Plains",
            "search_title": "",
            "source_url": "https://example.edu/sunflower",
            "final_url": "https://example.edu/sunflower",
            "snippet": "",
            "source_domain": "example.edu",
        }
        source_scope = infer_source_geo_scope(capture, {"level": "country", "name": "United States"})
        self.assertEqual(source_scope["scope"]["level"], "region")
        self.assertEqual(source_scope["scope"]["name"], "Northern Great Plains")
        self.assertEqual(source_scope["scope"]["geo_id"], "custom:region:northern_great_plains")
        self.assertEqual(source_scope["scope"]["geocode_confidence"], "approximate")

    def test_source_geo_scope_does_not_overwrite_global_claim_scope(self) -> None:
        capture = {
            "source_title": "Commercial Tomato Production Handbook | CAES Field Report",
            "search_title": "",
            "source_url": "https://fieldreport.caes.uga.edu/publications/B1312/commercial-tomato-production-handbook/",
            "final_url": "https://fieldreport.caes.uga.edu/publications/B1312/commercial-tomato-production-handbook/",
            "snippet": "University of Georgia Extension commercial tomato production handbook.",
            "source_domain": "fieldreport.caes.uga.edu",
        }
        claim_scope = infer_location_scope(
            "Ideal temperatures for tomato growth are 70-85 degrees F during the day.",
            capture,
            {"level": "country", "name": "United States"},
        )
        source_scope = infer_source_geo_scope(capture, {"level": "country", "name": "United States"})
        self.assertEqual(claim_scope["level"], "global")
        self.assertEqual(claim_scope["name"], "global")
        self.assertEqual(source_scope["scope"]["level"], "state")
        self.assertEqual(source_scope["scope"]["name"], "Georgia")
        self.assertEqual(source_scope["scope"]["geo_id"], "census:0400000US13")
        self.assertEqual(source_scope["scope"]["centroid"]["lat"], 32.629579)
        self.assertEqual(source_scope["scope"]["centroid"]["lon"], -83.423511)


if __name__ == "__main__":
    unittest.main()
