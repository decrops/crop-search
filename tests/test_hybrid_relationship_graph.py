from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from crop_search_framework.relationships import build_relationship_query_plan
from crop_search_framework import relationship_pipeline as rp
from crop_search_framework.schema_registry import SchemaRegistry


REPO = Path(__file__).resolve().parents[1]


def _tmp_repo(tmp: Path, *, copy_crops: bool = True) -> None:
    shutil.copytree(REPO / "schemas", tmp / "schemas")
    shutil.copytree(REPO / "config/relationships", tmp / "config/relationships")
    shutil.copytree(REPO / "config/source-tiers", tmp / "config/source-tiers")
    if copy_crops:
        shutil.copytree(REPO / "config/crops", tmp / "config/crops")


def _write_crop_universe(tmp: Path, count: int) -> None:
    crop_dir = tmp / "config/crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        crop_id = "crop{0:03d}".format(index)
        (crop_dir / "{0}.json".format(crop_id)).write_text(json.dumps({
            "crop_id": crop_id,
            "label": "Crop {0:03d}".format(index),
            "crop_group": "test_crop",
            "aliases": ["crop {0:03d}".format(index)],
            "scientific_names": ["Testus cropus {0:03d}".format(index)],
        }), encoding="utf-8")


def _claim(
    claim_id: str,
    *,
    subject: str = "",
    obj: str = "",
    subject_type: str = "crop",
    subject_node_id: str = "",
    object_type: str = "crop",
    object_node_id: str = "",
    effect: str = "beneficial",
    evidence: str = "Evidence text for a relationship claim.",
    mechanisms=None,
    context=None,
):
    subject_node_id = subject_node_id or subject
    object_node_id = object_node_id or obj
    claim = {
        "relationship_claim_id": claim_id,
        "run_id": "rel-hybrid",
        "subject_node_type": subject_type,
        "subject_node_id": subject_node_id,
        "object_node_type": object_type,
        "object_node_id": object_node_id,
        "relationship_mode": "rotation",
        "relationship_subtype": "previous_crop_effect",
        "direction": "object_precedes_subject",
        "ordered_pair_key": "{0}|{1}".format(subject_node_id, object_node_id),
        "canonical_relationship_key": "rotation|{0}|{1}".format(subject_node_id, object_node_id),
        "effect": effect,
        "claim_text": "Fixture relationship claim for testing.",
        "evidence_text": evidence,
        "value": {},
        "context": context or {},
        "provenance": {
            "source_urls": ["https://example.org/relationship"],
            "source_title": "Relationship fixture",
            "source_domain": "example.org",
            "document_type": "html",
            "source_tier_id": "extension_publication",
            "accessed_at": "2026-06-24T00:00:00Z",
            "extraction_method": "fixture",
        },
        "mechanisms": mechanisms or [],
        "confidence": "medium",
        "status": "accepted",
    }
    if subject_type == "crop":
        claim["subject_crop_id"] = subject
        claim["subject_crop_group"] = "vegetable"
    if object_type == "crop":
        claim["object_crop_id"] = obj
        claim["object_crop_group"] = "vegetable"
    return claim


def _write_claims(tmp: Path, claims) -> None:
    claims_dir = tmp / "exploration/relationships/claims/rel-hybrid"
    claims_dir.mkdir(parents=True, exist_ok=True)
    (claims_dir / "doc-1.json").write_text(json.dumps({"claims": claims}), encoding="utf-8")


def _write_discovery(tmp: Path) -> None:
    disc_dir = tmp / "exploration/relationships/discovery/rel-hybrid"
    disc_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "relationship_source_key": "rotation|cabbage|pak_choi|https://example.org",
        "canonical_relationship_key": "rotation|cabbage|pak_choi",
        "ordered_pair_key": "cabbage|pak_choi",
        "subject_crop_id": "cabbage",
        "object_crop_id": "pak_choi",
        "relationship_mode": "rotation",
        "relationship_subtype": "previous_crop_effect",
        "pair_mode": "unordered",
        "search_pair_key": "rotation|cabbage|pak_choi",
        "candidate_ordered_pair_keys": ["cabbage|pak_choi", "pak_choi|cabbage"],
        "source_url": "https://example.org",
        "source_tier": "extension_publication",
        "source_domain": "example.org",
        "score": 10,
        "access_status": "open_full_text",
        "discovery_drop_reason": "",
    }
    (disc_dir / "results.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")


class HybridRelationshipGraphTests(unittest.TestCase):
    def test_node_catalog_is_valid(self) -> None:
        catalog = rp.load_node_catalog(REPO)
        SchemaRegistry(REPO).validate("relationship-node-catalog.schema.json", catalog)
        major = {node["node_id"] for node in catalog["nodes"] if node["matrix_tier"] == "major_direct"}
        self.assertEqual(major, {
            "corn", "cotton", "rice", "soybean", "sunflower", "tomato", "wheat",
            "barley", "rapeseed", "sugar_beet", "potato",
        })

    def test_crop_claims_remain_valid_with_and_without_node_fields(self) -> None:
        registry = SchemaRegistry(REPO)
        old_style = _claim("old-style", subject="corn", obj="soybean")
        old_style.pop("subject_node_type")
        old_style.pop("subject_node_id")
        old_style.pop("object_node_type")
        old_style.pop("object_node_id")
        registry.validate("crop-relationship-claim.schema.json", old_style)
        registry.validate("crop-relationship-claim.schema.json", _claim("new-style", subject="corn", obj="soybean"))

    def test_unordered_pair_counts_for_planning_sizes(self) -> None:
        for crop_count, expected_pairs in ((7, 28), (25, 325), (120, 7260)):
            with self.subTest(crop_count=crop_count):
                with tempfile.TemporaryDirectory() as temp:
                    tmp = Path(temp)
                    _tmp_repo(tmp, copy_crops=False)
                    _write_crop_universe(tmp, crop_count)
                    plan = build_relationship_query_plan(
                        tmp,
                        mode_ids=["rotation"],
                        source_tier_ids=["extension_publication"],
                        queries_per_pair=1,
                        query_terms_per_source_tier=0,
                        pair_mode="unordered",
                    )
                self.assertEqual(plan["pair_mode"], "unordered")
                self.assertEqual(plan["planned_pair_count"], expected_pairs)
                self.assertEqual(plan["query_count"], expected_pairs)

    def test_ordered_matrix_population_accepts_claims_from_unordered_search_context(self) -> None:
        claims = [_claim("ordered-from-unordered", subject="wheat", obj="soybean", effect="beneficial")]
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            _tmp_repo(tmp)
            _write_claims(tmp, claims)
            _write_discovery(tmp)
            rp.populate_relationship_matrix(tmp, "rel-hybrid", mode_ids=["rotation"])
            matrix = json.loads((tmp / "exploration/relationships/matrix/populated-rel-hybrid.json").read_text())
        cells = {cell["ordered_pair_key"]: cell["mode_statuses"]["rotation"] for cell in matrix["cells"]}
        self.assertEqual(cells["wheat|soybean"]["status"], "evidence_found")
        self.assertEqual(cells["wheat|soybean"]["summary_effect"], "beneficial")

    def test_host_risk_caveat_overlays_direct_beneficial_evidence(self) -> None:
        claims = [
            _claim("direct-benefit", subject="pak_choi", obj="cabbage", effect="beneficial"),
            _claim(
                "clubroot-risk",
                subject_type="host_group",
                subject_node_id="clubroot_host",
                object_type="host_group",
                object_node_id="clubroot_host",
                effect="avoid",
                mechanisms=["disease_carryover"],
                context={"host_group": "clubroot_host", "risk_factor": "clubroot"},
            ),
        ]
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            _tmp_repo(tmp)
            _write_claims(tmp, claims)
            rp.build_relationship_graph(tmp, "rel-hybrid")
            resolved = rp.resolve_crop_relationship(tmp, "rel-hybrid", "pak choi", "cabbage")
        self.assertEqual(resolved["status"], "direct_evidence")
        self.assertEqual(resolved["primary_effect"], "beneficial")
        self.assertIn("host_risk_caveat", resolved["status_flags"])
        self.assertTrue(any(caveat.get("host_group") == "clubroot_host" for caveat in resolved["caveats"]))

    def test_resolver_infers_minor_crops_from_family_claim(self) -> None:
        claims = [
            _claim(
                "family-avoid",
                subject_type="botanical_family",
                subject_node_id="brassicaceae",
                object_type="botanical_family",
                object_node_id="brassicaceae",
                effect="avoid",
            )
        ]
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            _tmp_repo(tmp)
            _write_claims(tmp, claims)
            rp.build_relationship_graph(tmp, "rel-hybrid")
            resolved = rp.resolve_crop_relationship(tmp, "rel-hybrid", "pak choi", "cabbage")
        self.assertEqual(resolved["status"], "inferred_from_group")
        self.assertEqual(resolved["primary_effect"], "avoid")
        self.assertEqual(resolved["inference_basis"], "botanical_family")

    def test_resolver_infers_cross_group_rotation_from_functional_group_claim(self) -> None:
        # Cereal-after-legume: evidence is keyed on the subject's and object's
        # *different* functional groups, in rotation direction (wheat<-soybean).
        claims = [
            _claim(
                "cereal-after-legume",
                subject_type="functional_group",
                subject_node_id="cereal",
                object_type="functional_group",
                object_node_id="legume",
                effect="beneficial",
            )
        ]
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            _tmp_repo(tmp)
            _write_claims(tmp, claims)
            rp.build_relationship_graph(tmp, "rel-hybrid")
            resolved = rp.resolve_crop_relationship(tmp, "rel-hybrid", "wheat", "soybean")
        self.assertEqual(resolved["status"], "inferred_from_group")
        self.assertEqual(resolved["primary_effect"], "beneficial")
        self.assertEqual(resolved["inference_basis"], "functional_group")

    def test_resolver_prefers_family_basis_over_functional_group(self) -> None:
        # pak_choi and cabbage share both a family (brassicaceae) and a
        # functional group (leafy_brassica). Family is more specific and must
        # win, so the functional-group claim is not the one reported.
        claims = [
            _claim(
                "family-avoid",
                subject_type="botanical_family",
                subject_node_id="brassicaceae",
                object_type="botanical_family",
                object_node_id="brassicaceae",
                effect="avoid",
            ),
            _claim(
                "functional-benefit",
                subject_type="functional_group",
                subject_node_id="leafy_brassica",
                object_type="functional_group",
                object_node_id="leafy_brassica",
                effect="beneficial",
            ),
        ]
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            _tmp_repo(tmp)
            _write_claims(tmp, claims)
            rp.build_relationship_graph(tmp, "rel-hybrid")
            resolved = rp.resolve_crop_relationship(tmp, "rel-hybrid", "pak choi", "cabbage")
        self.assertEqual(resolved["status"], "inferred_from_group")
        self.assertEqual(resolved["inference_basis"], "botanical_family")
        self.assertEqual(resolved["primary_effect"], "avoid")

    def test_resolver_returns_no_evidence_for_unknown_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            _tmp_repo(tmp)
            _write_claims(tmp, [])
            rp.build_relationship_graph(tmp, "rel-hybrid")
            resolved = rp.resolve_crop_relationship(tmp, "rel-hybrid", "unknown crop", "wheat")
        self.assertEqual(resolved["status"], "no_evidence")
        self.assertEqual(resolved["unknown_nodes"], ["unknown crop"])

    def test_resolver_excludes_rejected_claims(self) -> None:
        claims = [_claim("rejected-direct", subject="pak_choi", obj="cabbage", effect="beneficial")]
        claims[0]["status"] = "rejected"
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            _tmp_repo(tmp)
            _write_claims(tmp, claims)
            rp.build_relationship_graph(tmp, "rel-hybrid")
            resolved = rp.resolve_crop_relationship(tmp, "rel-hybrid", "pak choi", "cabbage")
        self.assertEqual(resolved["status"], "no_evidence")
        self.assertEqual(resolved["unknown_nodes"], [])

    def test_routing_rule_detects_duplicate_relationship_parameter_spans(self) -> None:
        evidence = "Rotate wheat after soybean only when local disease pressure allows."
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            _tmp_repo(tmp)
            _write_claims(tmp, [_claim("relationship-span", subject="wheat", obj="soybean", evidence=evidence)])
            param_dir = tmp / "exploration/normalized/param-run"
            param_dir.mkdir(parents=True)
            (param_dir / "claim-1.json").write_text(json.dumps({
                "claim_id": "param-1",
                "parameter_id": "management.rotation_recommendation",
                "provenance": {"evidence_text": evidence},
            }), encoding="utf-8")
            report = rp.relationship_parameter_span_conflicts(tmp, "rel-hybrid", "param-run")
        self.assertEqual(report["conflict_count"], 1)
        self.assertEqual(report["conflicts"][0]["parameter_claim_id"], "param-1")


if __name__ == "__main__":
    unittest.main()
