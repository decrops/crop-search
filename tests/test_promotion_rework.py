from __future__ import annotations

import unittest

from crop_search_framework.review import review_claim, has_specific_evidence


def _claim(claim_id, *, tier="", title="Wheat field notes", block_type=None, value=None,
           evidence="Nitrogen requirements vary by yield goal and region."):
    value = value or {"value_type": "numeric", "raw_value_text": evidence, "numeric_value": 90, "unit": "kg/ha"}
    provenance = {
        "source_urls": ["https://x/"], "source_title": title, "source_domain": "x.org",
        "document_type": "html", "source_tier_id": tier,
    }
    if block_type:
        provenance["block_type"] = block_type
    return {
        "claim_id": claim_id,
        "entity": {"entity_type": "crop", "name": "wheat"},
        "parameter_id": "nutrients.nitrogen_requirement",
        "attribute": "nutrient_requirement", "attribute_subtype": "nitrogen_requirement",
        "claim_text": evidence, "evidence_text": evidence,
        "value": value,
        "location_scope": {"level": "global", "name": "global"},
        "source_geo_scope": {"level": "global", "name": "global"},
        "geo_evidence": {}, "time_scope": {"label": "growing season"},
        "provenance": provenance, "confidence": "medium", "conflict_status": "none",
    }


def _review(claim):
    cid = "c1"
    clusters = {cid: {"cluster_id": cid, "key": (), "claims": [claim]}}
    return review_claim(claim, cid, clusters)


class PromotionReworkTests(unittest.TestCase):
    def test_specific_evidence_detector(self):
        self.assertTrue(has_specific_evidence("applied 90 kg/ha nitrogen"))
        self.assertTrue(has_specific_evidence("optimum is 20 °C"))
        self.assertFalse(has_specific_evidence("nitrogen should be applied in spring"))

    def test_high_tier_reaches_canonical_where_untiered_would_not(self):
        # Same medium-confidence claim: a peer-reviewed tier promotes to canonical,
        # an untiered one lands in needs_review (tier precedence drains review).
        peer = _review(_claim("c1", tier="peer_reviewed_science"))
        untiered = _review(_claim("c2", tier=""))
        self.assertEqual(peer["decision"], "canonical_candidate")
        self.assertEqual(untiered["decision"], "needs_review")
        self.assertGreater(peer["quality_score"], untiered["quality_score"])

    def test_table_block_scores_higher_than_prose(self):
        table = _review(_claim("c1", tier="extension_publication", block_type="table"))
        prose = _review(_claim("c2", tier="extension_publication"))
        self.assertGreater(table["quality_score"], prose["quality_score"])

    def test_secondary_synthesis_not_promoted_to_canonical(self):
        # A meta-analysis at a high tier corroborates but should not be canonical.
        synth = _review(_claim("c1", tier="peer_reviewed_science",
                               title="Nitrogen use in wheat: a meta-analysis"))
        self.assertNotEqual(synth["decision"], "canonical_candidate")


if __name__ == "__main__":
    unittest.main()
