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


def _claim(cid, mode, subj, obj, *, effect="compatible", subtype="intercrop_compatibility",
           direction="simultaneous", node_type="crop", crop_fields=True) -> dict:
    claim = {
        "relationship_claim_id": cid, "run_id": "r",
        "subject_node_type": node_type, "subject_node_id": subj,
        "object_node_type": node_type, "object_node_id": obj,
        "relationship_mode": mode, "relationship_subtype": subtype, "direction": direction,
        "ordered_pair_key": "{0}|{1}".format(subj, obj),
        "canonical_relationship_key": "{0}|{1}|{2}".format(mode, subj, obj),
        "effect": effect, "claim_text": "intercrop test claim text",
        "evidence_text": "{0} {1} intercropping land equivalent ratio 1.3".format(subj, obj),
        "value": {}, "context": {},
        "provenance": {"source_urls": ["https://e.org"], "source_title": "t", "source_domain": "e.org",
                       "document_type": "html", "source_tier_id": "extension_publication",
                       "accessed_at": "2026-06-29T00:00:00Z", "extraction_method": "opus"},
        "confidence": "medium", "status": "accepted",
    }
    if crop_fields and node_type == "crop":
        claim["subject_crop_id"] = subj
        claim["object_crop_id"] = obj
        claim["subject_crop_group"] = "x"
        claim["object_crop_group"] = "y"
    return claim


def _write_claims(tmp: Path, run_id: str, claims) -> None:
    d = tmp / "exploration/relationships/claims" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "doc.json").write_text(json.dumps({"claims": claims}), encoding="utf-8")


class IntercropSymmetryTests(unittest.TestCase):
    def test_symmetric_claim_resolves_both_orderings(self) -> None:
        # Emitted in REVERSE order (soybean|corn) to prove code canonicalization.
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            _write_claims(tmp, "r", [_claim("ic1", "intercrop", "soybean", "corn", effect="beneficial")])
            graph = rp.build_relationship_graph(tmp, "r")
            ab = rp.resolve_crop_relationship(tmp, "r", "corn", "soybean", mode="intercrop")
            ba = rp.resolve_crop_relationship(tmp, "r", "soybean", "corn", mode="intercrop")
        self.assertIn("intercrop|corn|soybean", graph["direct"])   # canonicalized
        self.assertEqual(ab["status"], "direct_evidence")
        self.assertEqual(ba["status"], "direct_evidence")
        self.assertEqual(ab["primary_effect"], "beneficial")

    def test_directional_rotation_is_not_mirrored(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            _write_claims(tmp, "r", [_claim("r1", "rotation", "corn", "soybean",
                                            effect="beneficial", subtype="previous_crop_effect",
                                            direction="object_precedes_subject")])
            rp.build_relationship_graph(tmp, "r")
            ab = rp.resolve_crop_relationship(tmp, "r", "corn", "soybean", mode="rotation")
            ba = rp.resolve_crop_relationship(tmp, "r", "soybean", "corn", mode="rotation")
        self.assertEqual(ab["status"], "direct_evidence")
        self.assertEqual(ba["status"], "no_evidence")

    def test_relay_crop_directional_not_mirrored(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            _write_claims(tmp, "r", [_claim("rc1", "relay_crop", "corn", "soybean",
                                            subtype="relay_crop_window", direction="object_precedes_subject")])
            rp.build_relationship_graph(tmp, "r")
            ab = rp.resolve_crop_relationship(tmp, "r", "corn", "soybean", mode="relay_crop")
            ba = rp.resolve_crop_relationship(tmp, "r", "soybean", "corn", mode="relay_crop")
        self.assertEqual(ab["status"], "direct_evidence")
        self.assertEqual(ba["status"], "no_evidence")

    def test_symmetric_aggregate_inference_both_orderings(self) -> None:
        # functional_group cereal/legume intercrop claim, emitted reverse.
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            _write_claims(tmp, "r", [_claim("ag1", "intercrop", "legume", "cereal",
                                            node_type="functional_group", crop_fields=False,
                                            effect="beneficial")])
            rp.build_relationship_graph(tmp, "r")
            ab = rp.resolve_crop_relationship(tmp, "r", "corn", "soybean", mode="intercrop")
            ba = rp.resolve_crop_relationship(tmp, "r", "soybean", "corn", mode="intercrop")
        self.assertEqual(ab["status"], "inferred_from_group")
        self.assertEqual(ab["inference_basis"], "functional_group")
        self.assertEqual(ba["status"], "inferred_from_group")

    def test_matrix_mirrors_reverse_keyed_symmetric_claim(self) -> None:
        # The Gap B regression guard: a reverse-emitted key still fills both cells.
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            _write_claims(tmp, "r", [_claim("ic1", "intercrop", "soybean", "corn", effect="beneficial")])
            rp.populate_relationship_matrix(tmp, "r", mode_ids=["intercrop"])
            matrix = json.loads((tmp / "exploration/relationships/matrix/populated-r.json").read_text())
        cells = {c["ordered_pair_key"]: c["mode_statuses"]["intercrop"] for c in matrix["cells"]}
        self.assertEqual(cells["corn|soybean"]["status"], "evidence_found")
        self.assertEqual(cells["soybean|corn"]["status"], "evidence_found")


class PairModeAutoTests(unittest.TestCase):
    def _pair_mode(self, tmp, modes):
        return build_relationship_query_plan(
            tmp, mode_ids=modes, source_tier_ids=["extension_publication"],
            queries_per_pair=1, query_terms_per_source_tier=0, pair_mode="auto",
        )["pair_mode"]

    def test_auto_resolves_by_directionality(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            self.assertEqual(self._pair_mode(tmp, ["intercrop"]), "unordered")
            self.assertEqual(self._pair_mode(tmp, ["rotation"]), "ordered")
            self.assertEqual(self._pair_mode(tmp, ["intercrop", "companion_crop"]), "unordered")
            self.assertEqual(self._pair_mode(tmp, ["intercrop", "rotation"]), "ordered")


class SpanGuardTests(unittest.TestCase):
    def test_guard_accepts_supplied_parameter_id(self) -> None:
        evidence = "Maize bean intercropping raised the land equivalent ratio to 1.3."
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _tmp_repo(tmp)
            claim = _claim("ic1", "intercrop", "corn", "soybean")
            claim["evidence_text"] = evidence
            _write_claims(tmp, "r", [claim])
            param_dir = tmp / "exploration/normalized/param-run"
            param_dir.mkdir(parents=True)
            (param_dir / "c.json").write_text(json.dumps({
                "claim_id": "param-9",
                "parameter_id": "management.intercropping_compatibility",
                "provenance": {"evidence_text": evidence},
            }), encoding="utf-8")
            report = rp.relationship_parameter_span_conflicts(
                tmp, "r", "param-run", parameter_id="management.intercropping_compatibility")
        self.assertEqual(report["conflict_count"], 1)
        self.assertEqual(report["conflicts"][0]["parameter_claim_id"], "param-9")


if __name__ == "__main__":
    unittest.main()
