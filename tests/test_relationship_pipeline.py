from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from crop_search_framework import relationship_pipeline as rp

REPO = Path(__file__).resolve().parents[1]


def _tmp_repo(tmp: Path) -> None:
    shutil.copytree(REPO / "schemas", tmp / "schemas")
    shutil.copytree(REPO / "config/crops", tmp / "config/crops")
    shutil.copytree(REPO / "config/relationships", tmp / "config/relationships")
    (tmp / "config/fetch-policy").mkdir(parents=True)
    (tmp / "config/fetch-policy/default.json").write_text(json.dumps({
        "tier_trust": {"peer_reviewed_science": 1.0, "extension_publication": 0.8, "": 0.4},
        "per_parameter_target": 2, "low_tier_domain_cap": 1, "trusted_domain_cap": 50,
        "trusted_domains": ["fao.org", "doi.org"], "article_like_types": ["journal-article", "review"],
        "drop_relevance_gated": True,
    }), encoding="utf-8")


def _disc_row(**kw):
    base = {
        "query": "q", "subject_crop_id": kw["subj"], "object_crop_id": kw["obj"],
        "subject_crop_label": kw["subj"], "object_crop_label": kw["obj"],
        "relationship_mode": kw.get("mode", "rotation"), "relationship_subtype": kw.get("mode", "rotation"),
        "directionality": "directional",
        "ordered_pair_key": "{0}|{1}".format(kw["subj"], kw["obj"]),
        "canonical_relationship_key": "{0}|{1}|{2}".format(kw.get("mode", "rotation"), kw["subj"], kw["obj"]),
        "source_tier": kw.get("tier", "peer_reviewed_science"), "source_tier_label": "Peer",
        "provider": kw.get("provider", "openalex"), "discovery_rank": 1,
        "score": kw.get("score", 12), "score_components": {},
        "source_url": kw["url"], "source_key": kw["url"],
        "relationship_source_key": "{0}|{1}|{2}|{3}".format(kw.get("mode", "rotation"), kw["subj"], kw["obj"], kw["url"]),
        "doi": kw.get("doi", ""), "result_type": kw.get("rt", ""),
        "access_status": kw.get("access", "open_full_text"),
        "source_domain": kw.get("domain", "j.org"), "title": "t",
        "discovery_drop_reason": kw.get("drop", ""),
    }
    return base


def _write_ledger(tmp: Path, run_id: str, rows):
    d = tmp / "exploration/relationships/discovery" / run_id
    d.mkdir(parents=True)
    (d / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return d


class SelectTests(unittest.TestCase):
    def test_dedup_by_relationship_source_key_keeps_one_source_per_pair(self):
        # One URL evidences TWO pairs -> two distinct relationship_source_keys -> both selectable.
        rows = [
            _disc_row(subj="wheat", obj="soybean", url="https://fao.org/x", domain="fao.org"),
            _disc_row(subj="corn", obj="soybean", url="https://fao.org/x", domain="fao.org"),
        ]
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); _tmp_repo(tmp); d = _write_ledger(tmp, "rel-1", rows)
            summary = rp.select_relationship_fetch(tmp, "rel-1")
            queue = [json.loads(l) for l in (d / "fetch_queue.jsonl").read_text().splitlines()]
        self.assertEqual(summary["unique_source_pairs"], 2)
        self.assertEqual(summary["selected"], 2)
        self.assertEqual({r["canonical_relationship_key"] for r in queue},
                         {"rotation|wheat|soybean", "rotation|corn|soybean"})

    def test_prefilters_and_domain_cap(self):
        rows = [
            _disc_row(subj="wheat", obj="soybean", url="https://j.org/a", domain="j.org", doi="10.1/a", rt="dataset"),
            _disc_row(subj="wheat", obj="corn", url="https://low.org/1", domain="low.org", score=9),
            _disc_row(subj="wheat", obj="rice", url="https://low.org/2", domain="low.org", score=8),
            _disc_row(subj="rice", obj="corn", url="https://wiki/x", domain="en.wikipedia.org", provider="wikipedia", drop="relevance_gate"),
        ]
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); _tmp_repo(tmp); d = _write_ledger(tmp, "rel-1", rows)
            rp.select_relationship_fetch(tmp, "rel-1")
            q = {r["source_url"]: r for r in (json.loads(l) for l in (d / "fetch_queue.jsonl").read_text().splitlines())}
        self.assertEqual(q["https://j.org/a"]["fetch_skip_reason"], "non_article_type")
        self.assertEqual(q["https://wiki/x"]["fetch_skip_reason"], "relevance_gated")
        low = [r for r in q.values() if r["source_domain"] == "low.org"]
        self.assertEqual(sum(1 for r in low if r["fetch_selected"]), 1)  # cap=1
        self.assertIn("domain_cap", [r["fetch_skip_reason"] for r in low])


class MatrixTests(unittest.TestCase):
    def _claim(self, subj, obj, effect, mode="rotation", status="accepted"):
        return {
            "relationship_claim_id": "rc-{0}-{1}".format(subj, obj), "run_id": "rel-1",
            "subject_crop_id": subj, "object_crop_id": obj,
            "subject_crop_group": "cereal", "object_crop_group": "legume",
            "relationship_mode": mode, "relationship_subtype": mode, "direction": "object_precedes_subject",
            "ordered_pair_key": "{0}|{1}".format(subj, obj),
            "canonical_relationship_key": "{0}|{1}|{2}".format(mode, subj, obj),
            "effect": effect, "claim_text": "rotation effect text", "evidence_text": "evidence text here",
            "value": {"value_type": "text"}, "context": {}, "confidence": "high", "status": status,
            "provenance": {"source_urls": ["https://j.org/a"], "source_title": "T", "source_domain": "j.org",
                           "document_type": "html", "accessed_at": "2026-01-01T00:00:00Z", "extraction_method": "opus"},
        }

    def _setup(self, tmp, claims, searched):
        _tmp_repo(tmp)
        rows = [_disc_row(subj=s, obj=o, url="https://j.org/%s%s" % (s, o)) for s, o in searched]
        _write_ledger(tmp, "rel-1", rows)
        cdir = tmp / "exploration/relationships/claims/rel-1"; cdir.mkdir(parents=True)
        (cdir / "doc-1.json").write_text(json.dumps({"claims": claims}), encoding="utf-8")

    def test_statuses_evidence_searched_conflict(self):
        claims = [
            self._claim("wheat", "soybean", "beneficial"),
            self._claim("corn", "soybean", "beneficial"),
            self._claim("corn", "soybean", "avoid"),  # conflict on same pair+mode
        ]
        searched = [("wheat", "soybean"), ("corn", "soybean"), ("rice", "corn")]  # rice|corn searched, no claim
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); self._setup(tmp, claims, searched)
            summary = rp.populate_relationship_matrix(tmp, "rel-1", mode_ids=["rotation"])
            matrix = json.loads((tmp / "exploration/relationships/matrix/populated-rel-1.json").read_text())
        cells = {c["ordered_pair_key"]: c["mode_statuses"]["rotation"] for c in matrix["cells"]}
        self.assertEqual(cells["wheat|soybean"]["status"], "evidence_found")
        self.assertEqual(cells["wheat|soybean"]["summary_effect"], "beneficial")
        self.assertEqual(cells["corn|soybean"]["status"], "conflicting_evidence")
        self.assertEqual(cells["rice|corn"]["status"], "searched_no_evidence")
        # a pair never searched stays not_searched
        self.assertEqual(cells["tomato|cotton"]["status"], "not_searched")

    def test_eval_against_gold(self):
        claims = [self._claim("wheat", "soybean", "beneficial"), self._claim("corn", "soybean", "beneficial")]
        searched = [("wheat", "soybean"), ("corn", "soybean")]
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); self._setup(tmp, claims, searched)
            shutil.copytree(REPO / "tests/golden/relationships", tmp / "tests/golden/relationships")
            rp.populate_relationship_matrix(tmp, "rel-1", mode_ids=["rotation"])
            report = rp.eval_relationships(tmp, "rel-1")
        self.assertEqual(report["gold_records"], 2)
        self.assertEqual(report["metrics"]["pair_recall"], 1.0)
        self.assertEqual(report["metrics"]["effect_accuracy"], 1.0)


class FetchCorpusTests(unittest.TestCase):
    def test_fetch_then_build_corpus_maps_doc_to_pairs(self):
        from unittest import mock
        from crop_search_framework import relationship_pipeline
        from crop_search_framework.dev_tools.http_client import CachedResponse
        html = b"<html><head><title>Rotation study</title></head><body>" \
               b"<p>Wheat grown after soybean in rotation increased grain yield versus continuous wheat.</p></body></html>"
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); _tmp_repo(tmp)
            shutil.copytree(REPO / "config/parameters", tmp / "config/parameters")
            d = _write_ledger(tmp, "rel-1", [
                _disc_row(subj="wheat", obj="soybean", url="https://fao.org/x", domain="fao.org"),
                _disc_row(subj="corn", obj="soybean", url="https://fao.org/x", domain="fao.org"),
            ])
            relationship_pipeline.select_relationship_fetch(tmp, "rel-1")
            resp = CachedResponse(status_code=200, url="https://fao.org/x",
                                  headers={"content-type": "text/html"}, content=html)
            with mock.patch("crop_search_framework.dev_tools.http_client.HttpClient.get_binary", return_value=resp):
                fsum = relationship_pipeline.fetch_relationships(tmp, "rel-1", crop="wheat")
            csum = relationship_pipeline.build_relationship_corpus(tmp, "rel-1")
            hits = [json.loads(l) for l in (tmp / "exploration/relationships/corpus/rel-1/relationship_hits.jsonl").read_text().splitlines()]
        # one URL evidences 2 pairs -> 2 captures -> 1 unique document -> 2 relationship_hits
        self.assertEqual(fsum["captures_written"], 2)
        self.assertEqual(csum["unique_documents"], 1)
        self.assertEqual(len(hits), 2)
        self.assertEqual({h["canonical_relationship_key"] for h in hits},
                         {"rotation|wheat|soybean", "rotation|corn|soybean"})
        self.assertEqual(len({h["document_id"] for h in hits}), 1)  # same doc, both pairs


class ValidateClaimsTests(unittest.TestCase):
    def test_valid_and_invalid(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); _tmp_repo(tmp)
            cdir = tmp / "exploration/relationships/claims/rel-1"; cdir.mkdir(parents=True)
            good = MatrixTests()._claim("wheat", "soybean", "beneficial")
            bad = {"relationship_claim_id": "x", "subject_crop_id": "wheat"}  # missing required
            (cdir / "doc-1.json").write_text(json.dumps({"claims": [good, bad]}), encoding="utf-8")
            report = rp.validate_relationship_claims(tmp, "rel-1")
        self.assertEqual(report["valid_count"], 1)
        self.assertEqual(len(report["invalid"]), 1)


if __name__ == "__main__":
    unittest.main()
