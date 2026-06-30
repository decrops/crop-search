from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from crop_search_framework.relationships import build_relationship_query_plan
from crop_search_framework import relationship_pipeline as rp


REPO = Path(__file__).resolve().parents[1]


def _tmp_repo(tmp: Path) -> None:
    shutil.copytree(REPO / "schemas", tmp / "schemas")
    shutil.copytree(REPO / "config/crops", tmp / "config/crops")
    shutil.copytree(REPO / "config/relationships", tmp / "config/relationships")
    shutil.copytree(REPO / "config/source-tiers", tmp / "config/source-tiers")
    (tmp / "config/fetch-policy").mkdir(parents=True)
    (tmp / "config/fetch-policy/default.json").write_text(json.dumps({
        "tier_trust": {"textbook_reference": 1.0, "extension_publication": 0.8, "": 0.4},
        "per_parameter_target": 2, "low_tier_domain_cap": 1, "trusted_domain_cap": 50,
        "trusted_domains": ["fao.org", "doi.org"], "article_like_types": ["journal-article", "review", "book"],
        "drop_relevance_gated": True,
    }), encoding="utf-8")


def _aggregate_claim(claim_id: str, *, subject_id: str, object_id: str,
                     node_type: str = "functional_group", effect: str = "beneficial",
                     status: str = "accepted") -> dict:
    return {
        "relationship_claim_id": claim_id,
        "run_id": "rel-agg",
        "subject_node_type": node_type,
        "subject_node_id": subject_id,
        "object_node_type": node_type,
        "object_node_id": object_id,
        "relationship_mode": "rotation",
        "relationship_subtype": "group_previous_crop_effect",
        "direction": "object_precedes_subject",
        "ordered_pair_key": "{0}|{1}".format(subject_id, object_id),
        "canonical_relationship_key": "rotation|{0}:{1}|{0}:{2}".format(node_type, subject_id, object_id),
        "effect": effect,
        "claim_text": "Group-level rotation principle for testing.",
        "evidence_text": "A {0} crop after a {1} crop benefits from a residual nitrogen credit.".format(subject_id, object_id),
        "value": {},
        "context": {},
        "provenance": {
            "source_urls": ["https://example.org/agronomy-textbook"],
            "source_title": "Agronomy reference",
            "source_domain": "example.org",
            "document_type": "html",
            "source_tier_id": "textbook_reference",
            "accessed_at": "2026-06-29T00:00:00Z",
            "extraction_method": "opus",
        },
        "mechanisms": ["nitrogen"],
        "confidence": "medium",
        "status": status,
    }


def _write_claims(tmp: Path, run_id: str, claims) -> None:
    claims_dir = tmp / "exploration/relationships/claims" / run_id
    claims_dir.mkdir(parents=True, exist_ok=True)
    (claims_dir / "doc-1.json").write_text(json.dumps({"claims": claims}), encoding="utf-8")


def _write_ledger(tmp: Path, run_id: str, rows) -> Path:
    d = tmp / "exploration/relationships/discovery" / run_id
    d.mkdir(parents=True)
    (d / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return d


def _aggregate_ledger_row(**kw) -> dict:
    subj, obj, ntype = kw["subj"], kw["obj"], kw.get("ntype", "functional_group")
    canonical = "rotation|{0}:{1}|{0}:{2}".format(ntype, subj, obj)
    search_pair_key = "rotation|{0}:{1}|{0}:{2}".format(ntype, *sorted([subj, obj]))
    url = kw["url"]
    return {
        "query": "{0} after {1} rotation".format(subj, obj),
        # Aggregate rows legitimately have NO crop ids:
        "subject_crop_id": "", "object_crop_id": "",
        "subject_crop_label": "", "object_crop_label": "",
        "node_mode": "aggregate",
        "subject_node_type": ntype, "subject_node_id": subj,
        "object_node_type": ntype, "object_node_id": obj,
        "subject_search_label": subj, "object_search_label": obj,
        "relationship_mode": "rotation", "relationship_subtype": "group_previous_crop_effect",
        "directionality": "directional",
        "ordered_pair_key": "{0}|{1}".format(subj, obj),
        "canonical_relationship_key": canonical,
        "pair_mode": "ordered", "search_pair_key": search_pair_key,
        "candidate_ordered_pair_keys": ["{0}|{1}".format(subj, obj), "{0}|{1}".format(obj, subj)],
        "source_tier": kw.get("tier", "textbook_reference"), "source_tier_label": "Textbook",
        "provider": "openalex", "discovery_rank": 1,
        "score": kw.get("score", 14), "score_components": {},
        "source_url": url, "source_key": url,
        "relationship_source_key": "{0}|{1}".format(search_pair_key, url),
        "doi": "", "result_type": "",
        "access_status": "open_full_text",
        "source_domain": kw.get("domain", "fao.org"), "title": "principles",
        "discovery_drop_reason": "",
    }


class AggregateDiscoveryTests(unittest.TestCase):
    def test_aggregate_pair_counts(self) -> None:
        # 6 families + 6 functional groups (ordered n*n each) + 2 host self pairs.
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            ordered = build_relationship_query_plan(
                tmp, mode_ids=["rotation"], source_tier_ids=["textbook_reference"],
                queries_per_pair=1, query_terms_per_source_tier=0, node_mode="aggregate",
            )
            unordered = build_relationship_query_plan(
                tmp, mode_ids=["rotation"], source_tier_ids=["textbook_reference"],
                queries_per_pair=1, query_terms_per_source_tier=0, node_mode="aggregate",
                pair_mode="unordered",
            )
        self.assertEqual(ordered["node_mode"], "aggregate")
        self.assertEqual(ordered["planned_pair_count"], 6 * 6 + 6 * 6 + 2)   # 74
        self.assertEqual(unordered["planned_pair_count"], 21 + 21 + 2)       # 44

    def test_aggregate_queries_use_group_terms_and_no_crop_id(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            plan = build_relationship_query_plan(
                tmp, mode_ids=["rotation"], source_tier_ids=["textbook_reference"],
                queries_per_pair=3, query_terms_per_source_tier=0, node_mode="aggregate",
            )
        cross = [q for q in plan["queries"]
                 if q["subject_node_id"] == "cereal" and q["object_node_id"] == "legume"]
        self.assertTrue(cross)
        sample = cross[0]
        self.assertIn("cereal", sample["query"])
        self.assertIn("legume", sample["query"])
        self.assertEqual(sample["subject_crop_id"], "")
        self.assertEqual(sample["object_crop_id"], "")
        self.assertEqual(sample["subject_node_type"], "functional_group")
        self.assertEqual(sample["subject_search_label"], "cereal")
        self.assertEqual(sample["object_search_label"], "legume")
        self.assertEqual(sample["canonical_relationship_key"],
                         "rotation|functional_group:cereal|functional_group:legume")

    def test_crop_mode_items_still_carry_crop_labels(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            plan = build_relationship_query_plan(
                tmp, mode_ids=["rotation"], source_tier_ids=["extension_publication"],
                queries_per_pair=1, query_terms_per_source_tier=0,
            )
        sample = plan["queries"][0]
        self.assertEqual(plan["node_mode"], "crop")
        self.assertEqual(sample["node_mode"], "crop")
        self.assertTrue(sample["subject_crop_label"])
        self.assertTrue(sample["subject_search_label"])
        self.assertEqual(sample["subject_node_type"], "crop")

    def test_fetch_queue_survives_aggregate_rows_without_crop_ids(self) -> None:
        rows = [
            _aggregate_ledger_row(subj="cereal", obj="legume", url="https://fao.org/principles"),
            _aggregate_ledger_row(subj="brassicaceae", obj="brassicaceae", ntype="botanical_family",
                                  url="https://fao.org/brassica"),
        ]
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            d = _write_ledger(tmp, "rel-agg", rows)
            summary = rp.select_relationship_fetch(tmp, "rel-agg")
            queue = [json.loads(l) for l in (d / "fetch_queue.jsonl").read_text().splitlines()]
        self.assertEqual(summary["selected"], 2)
        selected = [r for r in queue if r["fetch_selected"]]
        self.assertEqual({r["subject_node_id"] for r in selected}, {"cereal", "brassicaceae"})
        self.assertTrue(all(r["node_mode"] == "aggregate" for r in selected))
        self.assertTrue(all(r["subject_crop_id"] == "" for r in selected))

    def test_aggregate_claim_feeds_resolver_inference(self) -> None:
        # Production -> consumption: a functional-group claim makes the resolver
        # infer a minor/uncovered crop pair (wheat after soybean) from cereal<-legume.
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            _write_claims(tmp, "rel-agg", [
                _aggregate_claim("cereal-after-legume", subject_id="cereal", object_id="legume"),
            ])
            graph = rp.build_relationship_graph(tmp, "rel-agg")
            resolved = rp.resolve_crop_relationship(tmp, "rel-agg", "wheat", "soybean")
        # Graph indexes aggregates by node id (mode|subject_id|object_id).
        self.assertIn("rotation|cereal|legume", graph["aggregate"])
        self.assertEqual(resolved["status"], "inferred_from_group")
        self.assertEqual(resolved["inference_basis"], "functional_group")
        self.assertEqual(resolved["primary_effect"], "beneficial")


if __name__ == "__main__":
    unittest.main()
