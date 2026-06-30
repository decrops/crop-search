from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crop_search_framework import relationships as R
from crop_search_framework import relationship_pipeline as rp
from crop_search_framework import source_tiers as st
from crop_search_framework.dev_tools import discovery_connectors as dc
from crop_search_framework.schema_registry import SchemaRegistry

REPO = Path(__file__).resolve().parents[1]


def _tmp_repo(tmp: Path) -> None:
    shutil.copytree(REPO / "schemas", tmp / "schemas")
    shutil.copytree(REPO / "config/crops", tmp / "config/crops")
    shutil.copytree(REPO / "config/relationships", tmp / "config/relationships")
    shutil.copytree(REPO / "config/source-tiers", tmp / "config/source-tiers")


def _claim(cid, subj, obj, effect, tier, *, mode="rotation", status="accepted"):
    return {
        "relationship_claim_id": cid,
        "run_id": "r",
        "subject_node_type": "crop",
        "subject_node_id": subj,
        "object_node_type": "crop",
        "object_node_id": obj,
        "subject_crop_id": subj,
        "object_crop_id": obj,
        "subject_crop_group": "cereal",
        "object_crop_group": "legume",
        "relationship_mode": mode,
        "relationship_subtype": "previous_crop_effect",
        "direction": "object_precedes_subject",
        "ordered_pair_key": "{0}|{1}".format(subj, obj),
        "canonical_relationship_key": "{0}|{1}|{2}".format(mode, subj, obj),
        "effect": effect,
        "claim_text": "{0} relates to {1} via {2} with effect {3}.".format(subj, obj, mode, effect),
        "evidence_text": "Reported {0}-{1} {2} relationship in the source text.".format(subj, obj, mode),
        "value": {},
        "context": {},
        "provenance": {
            "source_urls": ["https://e.org/x"],
            "source_title": "t",
            "source_domain": "e.org",
            "document_type": "html",
            "source_tier_id": tier,
            "accessed_at": "2026-06-30T00:00:00Z",
            "extraction_method": "fixture",
        },
        "confidence": "medium",
        "status": status,
    }


def _write_claims(tmp: Path, run_id: str, claims) -> None:
    d = tmp / "exploration/relationships/claims" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "claims.json").write_text(json.dumps({"claims": claims}), encoding="utf-8")


# --------------------------------------------------------------------------- #
# A2 — reference_encyclopedia connector routing
# --------------------------------------------------------------------------- #
class ConnectorRoutingTests(unittest.TestCase):
    def test_reference_encyclopedia_routes_to_wikipedia(self):
        sentinel = [{"title": "wiki"}]
        with patch.object(dc, "wikipedia_results", return_value=sentinel) as wiki:
            results, errors = dc.connector_results_for_tier("q", "corn", "reference_encyclopedia", 5, "ua")
        self.assertEqual(results, sentinel)
        self.assertEqual(errors, [])
        wiki.assert_called_once()

    def test_unknown_tier_returns_empty(self):
        self.assertEqual(dc.connector_results_for_tier("q", "corn", "made_up_tier", 5, "ua"), ([], []))


# --------------------------------------------------------------------------- #
# A3 — tier ranking helpers
# --------------------------------------------------------------------------- #
class TierHelperTests(unittest.TestCase):
    def test_rank_index_and_bands(self):
        ri = st.tier_rank_index(REPO)
        self.assertEqual(ri["peer_reviewed_science"], 1)
        self.assertEqual(ri["reference_encyclopedia"], 6)
        # unknown tier ranks worst (max + 1)
        self.assertEqual(st.tier_rank("nope", ri), max(ri.values()) + 1)
        self.assertEqual(st.tier_band("peer_reviewed_science"), "evidence")
        self.assertEqual(st.tier_band("textbook_reference"), "backbone")
        self.assertEqual(st.tier_band("reference_encyclopedia"), "backbone")

    def test_reference_encyclopedia_not_in_default_discovery_order(self):
        m = st.load_source_tier_manifest(REPO, st.DEFAULT_SOURCE_TIER_MANIFEST_PATH)
        order = m["policies"][0]["tier_order"]
        self.assertNotIn("reference_encyclopedia", order)
        self.assertIn("reference_encyclopedia", {t["tier_id"] for t in m["tiers"]})


# --------------------------------------------------------------------------- #
# A4 — tiered_effect polarity + supersession
# --------------------------------------------------------------------------- #
class TieredEffectTests(unittest.TestCase):
    def setUp(self):
        self.ri = st.tier_rank_index(REPO)

    def _c(self, effect, tier, status="accepted"):
        return {"effect": effect, "status": status, "provenance": {"source_tier_id": tier}}

    def test_peer_reviewed_beats_textbook(self):
        out = rp.tiered_effect(
            [self._c("compatible", "textbook_reference"), self._c("incompatible", "peer_reviewed_science")],
            self.ri,
        )
        self.assertEqual(out["summary_effect"], "incompatible")
        self.assertEqual(out["evidence_grade"], "peer_reviewed")
        self.assertEqual(out["best_source_tier"], "peer_reviewed_science")
        self.assertTrue(out["tier_superseded_conflict"])
        self.assertEqual(out["status"], "evidence_found")

    def test_top_tier_internal_conflict(self):
        out = rp.tiered_effect(
            [self._c("beneficial", "peer_reviewed_science"), self._c("avoid", "peer_reviewed_science")],
            self.ri,
        )
        self.assertEqual(out["status"], "conflicting_evidence")
        # conflict_count = dissenting CLAIMS against the summary, not distinct labels.
        self.assertEqual(out["conflict_count"], 1)
        self.assertFalse(out["tier_superseded_conflict"])

    def test_conditional_plus_decisive_is_ambiguous(self):
        out = rp.tiered_effect(
            [self._c("beneficial", "textbook_reference"), self._c("conditional", "textbook_reference")],
            self.ri,
        )
        self.assertEqual(out["summary_effect"], "conditional")
        self.assertTrue(out["ambiguous_effect"])
        self.assertEqual(out["status"], "evidence_found")

    def test_neutral_only(self):
        out = rp.tiered_effect([self._c("neutral", "extension_publication")], self.ri)
        self.assertEqual(out["summary_effect"], "neutral")
        self.assertEqual(out["evidence_grade"], "reference_backbone")
        self.assertFalse(out["ambiguous_effect"])

    def test_lower_tier_agreement_is_not_a_conflict(self):
        out = rp.tiered_effect(
            [self._c("compatible", "peer_reviewed_science"), self._c("beneficial", "textbook_reference")],
            self.ri,
        )
        self.assertFalse(out["tier_superseded_conflict"])
        self.assertEqual(out["evidence_grade"], "peer_reviewed")


# --------------------------------------------------------------------------- #
# A6 — aggregate-node-type scoping
# --------------------------------------------------------------------------- #
class AggregateScopeTests(unittest.TestCase):
    def test_functional_group_only_pair_counts(self):
        nodes = R.load_aggregate_nodes(REPO, ["functional_group"])
        self.assertTrue(all(n.node_type == "functional_group" for n in nodes))
        ordered = R.aggregate_node_pairs(nodes, pair_mode="ordered", include_self_pairs=True)
        unordered = R.aggregate_node_pairs(nodes, pair_mode="unordered", include_self_pairs=True)
        # 6 functional groups -> 6*6 directional incl self, 6*7/2 symmetric incl self
        self.assertEqual(len(ordered), 36)
        self.assertEqual(len(unordered), 21)

    def test_default_loads_all_three_types(self):
        nodes = R.load_aggregate_nodes(REPO)
        self.assertEqual({n.node_type for n in nodes}, {"botanical_family", "functional_group", "host_group"})


# --------------------------------------------------------------------------- #
# A7 — source_tier_id enforcement
# --------------------------------------------------------------------------- #
class TierEnforcementTests(unittest.TestCase):
    def test_schema_rejects_missing_tier(self):
        claim = _claim("c1", "corn", "soybean", "beneficial", "peer_reviewed_science")
        del claim["provenance"]["source_tier_id"]
        with self.assertRaises(ValueError):
            SchemaRegistry(REPO).validate("crop-relationship-claim.schema.json", claim)

    def test_validation_drops_unknown_tier(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            _write_claims(tmp, "r1", [
                _claim("good", "corn", "soybean", "beneficial", "peer_reviewed_science"),
                _claim("bad", "corn", "wheat", "beneficial", "totally_made_up_tier"),
            ])
            report = rp.validate_relationship_claims(tmp, "r1")
            ids = {c["relationship_claim_id"] for c in report["claims"]}
            self.assertIn("good", ids)
            self.assertNotIn("bad", ids)
            self.assertTrue(any("unknown source_tier_id" in m for m in report["invalid"]))


# --------------------------------------------------------------------------- #
# A8 — merged graph supersession across runs
# --------------------------------------------------------------------------- #
class MergedGraphTests(unittest.TestCase):
    def test_upgrade_in_one_run_supersedes_backbone_in_another(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            _write_claims(tmp, "backbone", [_claim("b1", "corn", "soybean", "compatible", "textbook_reference")])
            _write_claims(tmp, "upgrade", [_claim("u1", "corn", "soybean", "incompatible", "peer_reviewed_science")])
            graph = rp.build_merged_relationship_graph(tmp, ["backbone", "upgrade"])
            self.assertEqual(graph["claim_count"], 2)
            catalog = rp.load_node_catalog(tmp)
            ri = st.tier_rank_index(tmp)
            res = rp._resolve(graph, catalog, ri, "corn", "soybean", "rotation")
            self.assertEqual(res["status"], "direct_evidence")
            self.assertEqual(res["primary_effect"], "incompatible")
            self.assertEqual(res["evidence_grade"], "peer_reviewed")
            self.assertTrue(res["tier_superseded_conflict"])

    def test_dedupe_by_claim_id(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            shared = _claim("dup", "corn", "soybean", "beneficial", "peer_reviewed_science")
            _write_claims(tmp, "a", [shared])
            _write_claims(tmp, "b", [shared])
            graph = rp.build_merged_relationship_graph(tmp, ["a", "b"])
            self.assertEqual(graph["claim_count"], 1)


# --------------------------------------------------------------------------- #
# A9 — coverage report accepted vs provisional split
# --------------------------------------------------------------------------- #
class CoverageReportTests(unittest.TestCase):
    def test_accepted_vs_provisional_split(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            _write_claims(tmp, "run", [
                _claim("acc", "corn", "soybean", "beneficial", "peer_reviewed_science", status="accepted"),
                _claim("prov", "corn", "wheat", "beneficial", "textbook_reference", status="needs_review"),
            ])
            report = rp.relationship_coverage_report(tmp, ["run"], modes=("rotation",))
            rot = report["modes_detail"]["rotation"]
            self.assertEqual(report["total_pairs"], 55)
            self.assertEqual(rot["accepted"]["answerable"], 1)
            self.assertEqual(rot["provisional"]["answerable"], 2)
            # the needs_review backbone pair is not an accepted upgrade candidate
            self.assertEqual(rot["upgrade_candidates"], [])

    def test_accepted_backbone_pair_is_upgrade_candidate(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            _write_claims(tmp, "run", [
                _claim("bb", "corn", "soybean", "compatible", "textbook_reference", status="accepted"),
            ])
            report = rp.relationship_coverage_report(tmp, ["run"], modes=("rotation",))
            rot = report["modes_detail"]["rotation"]
            self.assertEqual(rot["accepted"]["reference_backbone"], 1)
            self.assertIn("corn|soybean", rot["upgrade_candidates"])


# --------------------------------------------------------------------------- #
# Review fixes — masking, supersede flag, grade, neutral tie, directional, dedup
# --------------------------------------------------------------------------- #
class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.ri = st.tier_rank_index(REPO)

    def _c(self, effect, tier):
        return {"effect": effect, "status": "accepted", "provenance": {"source_tier_id": tier}}

    def test_all_unknown_top_tier_does_not_mask_lower_tier(self):
        # HIGH 1: peer-reviewed `unknown` must not bury a textbook `beneficial`.
        out = rp.tiered_effect(
            [self._c("unknown", "peer_reviewed_science"), self._c("beneficial", "extension_publication")],
            self.ri,
        )
        self.assertEqual(out["summary_effect"], "beneficial")
        self.assertEqual(out["best_source_tier"], "extension_publication")
        self.assertEqual(out["evidence_grade"], "reference_backbone")

    def test_supersede_flag_under_noncommittal_summary(self):
        # HIGH 2: a peer-reviewed `conditional`/`unknown` overriding a lower `avoid`
        # must raise the supersede flag instead of silently dropping the warning.
        cond = rp.tiered_effect(
            [self._c("conditional", "peer_reviewed_science"), self._c("avoid", "extension_publication")],
            self.ri,
        )
        self.assertEqual(cond["summary_effect"], "conditional")
        self.assertTrue(cond["tier_superseded_conflict"])

    def test_grade_follows_deciding_tier_not_best_present(self):
        # HIGH 3: effect from extension + an unknown peer-reviewed claim -> backbone.
        out = rp.tiered_effect(
            [self._c("beneficial", "extension_publication"), self._c("unknown", "peer_reviewed_science")],
            self.ri,
        )
        self.assertEqual(out["evidence_grade"], "reference_backbone")
        self.assertEqual(out["best_source_tier"], "extension_publication")

    def test_neutral_does_not_beat_decisive_on_tie(self):
        a = rp.tiered_effect([self._c("neutral", "textbook_reference"), self._c("beneficial", "textbook_reference")], self.ri)
        b = rp.tiered_effect([self._c("beneficial", "textbook_reference"), self._c("neutral", "textbook_reference")], self.ri)
        self.assertEqual(a["summary_effect"], "beneficial")
        self.assertEqual(b["summary_effect"], "beneficial")  # order-independent

    def test_conflict_count_is_dissenting_claims(self):
        out = rp.tiered_effect(
            [self._c("beneficial", "peer_reviewed_science")] * 3 + [self._c("avoid", "peer_reviewed_science")],
            self.ri,
        )
        self.assertEqual(out["conflict_count"], 1)

    def test_directional_upgrade_candidates_are_per_direction(self):
        # Fix 4: peer-reviewed one way, backbone the other -> backbone direction listed.
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            _write_claims(tmp, "run", [
                _claim("fwd", "corn", "soybean", "beneficial", "peer_reviewed_science", status="accepted"),
                _claim("rev", "soybean", "corn", "compatible", "textbook_reference", status="accepted"),
            ])
            rot = rp.relationship_coverage_report(tmp, ["run"], modes=("rotation",))["modes_detail"]["rotation"]
            self.assertEqual(rot["upgrade_candidates"], ["soybean|corn"])

    def test_single_run_graph_dedupes_duplicate_claim_id(self):
        # Fix 5: a duplicated id must not manufacture a phantom conflict.
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            dup = _claim("dup", "corn", "soybean", "beneficial", "peer_reviewed_science")
            _write_claims(tmp, "run", [dup, dict(dup)])
            graph = rp.build_relationship_graph(tmp, "run")
            self.assertEqual(len(graph["direct"]["rotation|corn|soybean"]), 1)

    def test_coverage_surfaces_catalog_missing_crops(self):
        # Fix 8: a crop with no catalog node must be reported, not silently `none`.
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            cat_path = tmp / "config/relationships/node-catalog.json"
            cat = json.loads(cat_path.read_text())
            cat["nodes"] = [n for n in cat["nodes"] if not (n.get("node_type") == "crop" and n.get("node_id") == "tomato")]
            cat_path.write_text(json.dumps(cat), encoding="utf-8")
            _write_claims(tmp, "run", [_claim("c", "corn", "soybean", "beneficial", "peer_reviewed_science")])
            report = rp.relationship_coverage_report(tmp, ["run"], modes=("rotation",))
            self.assertIn("tomato", report["unknown_crops"])


if __name__ == "__main__":
    unittest.main()
