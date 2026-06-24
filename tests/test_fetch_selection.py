from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from crop_search_framework import fetch_selection


POLICY = {
    "tier_trust": {"peer_reviewed_science": 1.0, "extension_publication": 0.8, "": 0.4},
    "per_parameter_target": 2,
    "low_tier_domain_cap": 1,
    "trusted_domain_cap": 50,
    "trusted_domains": ["fao.org"],
    "article_like_types": ["journal-article", "review"],
    "drop_relevance_gated": True,
}


def _row(**kw):
    base = {
        "ledger_id": kw.get("ledger_id", "L"),
        "query": "q", "parameter_id": kw.get("parameter_id", "p1"),
        "source_tier": kw.get("source_tier", "extension_publication"),
        "provider": kw.get("provider", "openalex"),
        "discovery_rank": 1, "score": kw.get("score", 10),
        "score_components": {},
        "source_url": kw["source_url"],
        "canonical_key": kw["canonical_key"],
        "doi": kw.get("doi", ""), "result_type": kw.get("result_type", ""),
        "access_status": kw.get("access_status", "open_full_text"),
        "source_domain": kw["source_domain"],
        "title": "t", "discovery_drop_reason": kw.get("drop", ""),
    }
    return base


class FetchSelectionTests(unittest.TestCase):
    def _setup(self, tmp: Path, ledger_rows):
        (tmp / "config/fetch-policy").mkdir(parents=True)
        (tmp / "config/fetch-policy/default.json").write_text(json.dumps(POLICY), encoding="utf-8")
        d = tmp / "exploration/discovery/run-1"
        d.mkdir(parents=True)
        (d / "results.jsonl").write_text("\n".join(json.dumps(r) for r in ledger_rows) + "\n", encoding="utf-8")
        return d

    def test_dedup_preserves_many_to_many(self):
        rows = [
            _row(ledger_id="L1", canonical_key="doi:10.1/a", source_url="https://j/a",
                 source_domain="j.org", doi="10.1/a", result_type="journal-article", parameter_id="p1"),
            _row(ledger_id="L2", canonical_key="doi:10.1/a", source_url="https://doi.org/10.1/a",
                 source_domain="doi.org", doi="10.1/a", result_type="journal-article", parameter_id="p2"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            d = self._setup(tmp, rows)
            fetch_selection.select_fetch_queue(tmp, "run-1")
            queue = [json.loads(l) for l in (d / "fetch_queue.jsonl").read_text().splitlines()]
        self.assertEqual(len(queue), 1)  # one unique source
        row = queue[0]
        self.assertEqual(sorted(row["parameter_ids"]), ["p1", "p2"])
        self.assertEqual(sorted(row["ledger_ids"]), ["L1", "L2"])
        self.assertTrue(row["fetch_selected"])

    def test_tier_aware_domain_caps(self):
        # Two low-tier rows on the same capped domain (cap=1), plus a trusted FAO domain.
        rows = [
            _row(ledger_id="A", canonical_key="url:low/1", source_url="https://low.org/1",
                 source_domain="low.org", parameter_id="p1", score=9),
            _row(ledger_id="B", canonical_key="url:low/2", source_url="https://low.org/2",
                 source_domain="low.org", parameter_id="p2", score=8),
            _row(ledger_id="C", canonical_key="url:fao/1", source_url="https://fao.org/1",
                 source_domain="fao.org", parameter_id="p3", score=5),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            d = self._setup(tmp, rows)
            fetch_selection.select_fetch_queue(tmp, "run-1")
            queue = {r["canonical_key"]: r for r in (json.loads(l) for l in (d / "fetch_queue.jsonl").read_text().splitlines())}
        # low.org capped at 1: first selected, second skipped via domain_cap
        low = [r for r in queue.values() if r["source_domain"] == "low.org"]
        self.assertEqual(sum(1 for r in low if r["fetch_selected"]), 1)
        self.assertIn("domain_cap", [r["fetch_skip_reason"] for r in low])
        # fao.org trusted -> selected despite lower score
        self.assertTrue(queue["url:fao/1"]["fetch_selected"])

    def test_prefilters_junk_nonarticle_and_relevance_gated(self):
        rows = [
            _row(ledger_id="J", canonical_key="doi:10.1/supp", source_url="https://j/supp",
                 source_domain="j.org", doi="10.1/x/supp-1", result_type="journal-article", parameter_id="p1"),
            _row(ledger_id="D", canonical_key="doi:10.1/data", source_url="https://j/data",
                 source_domain="j.org", doi="10.1/data", result_type="dataset", parameter_id="p2"),
            _row(ledger_id="G", canonical_key="url:wiki/x", source_url="https://en.wikipedia.org/x",
                 source_domain="en.wikipedia.org", parameter_id="p3", provider="wikipedia",
                 access_status="open_full_text", drop="relevance_gate"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            d = self._setup(tmp, rows)
            summary = fetch_selection.select_fetch_queue(tmp, "run-1")
            queue = {r["canonical_key"]: r for r in (json.loads(l) for l in (d / "fetch_queue.jsonl").read_text().splitlines())}
        self.assertEqual(queue["doi:10.1/supp"]["fetch_skip_reason"], "junk_doi")
        self.assertEqual(queue["doi:10.1/data"]["fetch_skip_reason"], "non_article_type")
        self.assertEqual(queue["url:wiki/x"]["fetch_skip_reason"], "relevance_gated")
        self.assertEqual(summary["selected"], 0)


if __name__ == "__main__":
    unittest.main()
