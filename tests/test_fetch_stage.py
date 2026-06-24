from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crop_search_framework import fetch_stage
from crop_search_framework.dev_tools.http_client import CachedResponse

REPO = Path(__file__).resolve().parents[1]

HTML = b"<html><head><title>Wheat N guide</title></head><body>" \
       b"<p>Apply nitrogen at 90 kg/ha for a 60 bushel yield goal in winter wheat.</p></body></html>"


def _seed_discovery(repo: Path, run_id: str):
    d = repo / "exploration" / "discovery" / run_id
    d.mkdir(parents=True)
    ledger = [
        {"ledger_id": "L1", "query": "wheat nitrogen kg/ha", "parameter_id": "nutrients.nitrogen_requirement",
         "source_tier": "extension_publication"},
        {"ledger_id": "L2", "query": "wheat N timing", "parameter_id": "nutrients.nitrogen_timing",
         "source_tier": "extension_publication"},
        {"ledger_id": "L3", "query": "paywalled study", "parameter_id": "stress.cold_tolerance",
         "source_tier": "peer_reviewed_science"},
    ]
    (d / "results.jsonl").write_text("\n".join(json.dumps(r) for r in ledger) + "\n", encoding="utf-8")
    queue = [
        {"canonical_key": "url:ex.org/n", "source_url": "https://ex.org/n", "doi": "",
         "source_domain": "ex.org", "source_tier": "extension_publication", "access_status": "open_full_text",
         "parameter_ids": ["nutrients.nitrogen_requirement", "nutrients.nitrogen_timing"],
         "ledger_ids": ["L1", "L2"], "fetch_selected": True, "fetch_skip_reason": ""},
        {"canonical_key": "doi:10.1/paywall", "source_url": "https://doi.org/10.1/paywall", "doi": "10.1/paywall",
         "source_domain": "doi.org", "source_tier": "peer_reviewed_science", "access_status": "metadata_only",
         "parameter_ids": ["stress.cold_tolerance"], "ledger_ids": ["L3"], "fetch_selected": True, "fetch_skip_reason": ""},
        {"canonical_key": "url:ex.org/skip", "source_url": "https://ex.org/skip", "doi": "",
         "source_domain": "ex.org", "source_tier": "industry_grower_guide", "access_status": "open_full_text",
         "parameter_ids": ["soil.ph_range"], "ledger_ids": [], "fetch_selected": False, "fetch_skip_reason": "domain_cap"},
    ]
    (d / "fetch_queue.jsonl").write_text("\n".join(json.dumps(r) for r in queue) + "\n", encoding="utf-8")


def _repo_with_config(tmp: Path) -> Path:
    shutil.copytree(REPO / "schemas", tmp / "schemas")
    (tmp / "config/parameters").mkdir(parents=True)
    shutil.copy(REPO / "config/parameters/core-crop-parameters.json", tmp / "config/parameters/core-crop-parameters.json")
    (tmp / "config/crops").mkdir(parents=True)
    shutil.copy(REPO / "config/crops/wheat.json", tmp / "config/crops/wheat.json")
    cfg = {
        "run_id": "run-fetch", "version": "0.1.0", "crop": "wheat",
        "region_scope": {"level": "global", "name": "global"},
        "parameter_manifest_path": "config/parameters/core-crop-parameters.json",
        "crop_profile_path": "config/crops/wheat.json",
        "max_results_per_query": 3, "tool_bindings": {"search": "s", "fetch": "f", "parse": "p"},
    }
    (tmp / "run.json").write_text(json.dumps(cfg), encoding="utf-8")
    return tmp / "run.json"


class FetchStageTests(unittest.TestCase):
    def test_fetch_executor_produces_captures(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            run_cfg = _repo_with_config(tmp)
            _seed_discovery(tmp, "run-fetch")
            resp = CachedResponse(status_code=200, url="https://ex.org/n",
                                  headers={"content-type": "text/html"}, content=HTML)
            with mock.patch.object(fetch_stage.HttpClient, "get_binary", return_value=resp):
                summary = fetch_stage.run_fetch(tmp, run_cfg)
            raw = tmp / "exploration/raw/run-fetch"
            captures = {json.loads(p.read_text())["id"]: json.loads(p.read_text())
                        for p in raw.glob("*.json") if p.name != "summary.json"}

        # 2 params for the fetched doc + 1 metadata-only = 3 captures
        self.assertEqual(summary["captures_written"], 3)
        self.assertEqual(summary["fetch_successes"], 1)
        self.assertEqual(summary["unique_sources_captured"], 2)
        # the open doc yields parsed text + candidate claims for both params
        open_caps = [c for c in captures.values() if c["access_status"] == "open_full_text"]
        self.assertEqual(len(open_caps), 2)
        self.assertIn("90 kg/ha", open_caps[0]["raw_text"])
        self.assertEqual({c["parameter_id"] for c in open_caps},
                         {"nutrients.nitrogen_requirement", "nutrients.nitrogen_timing"})
        # queries recovered from the ledger
        self.assertTrue(all(c["query"] for c in open_caps))
        # paywalled metadata-only candidate is captured but not fetched
        meta = [c for c in captures.values() if c["access_status"] == "metadata_only"]
        self.assertEqual(len(meta), 1)
        self.assertEqual(meta[0]["raw_text"], "")
        self.assertEqual(meta[0]["parameter_id"], "stress.cold_tolerance")

    def test_captures_feed_build_corpus(self):
        from crop_search_framework import corpus
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            run_cfg = _repo_with_config(tmp)
            _seed_discovery(tmp, "run-fetch")
            resp = CachedResponse(status_code=200, url="https://ex.org/n",
                                  headers={"content-type": "text/html"}, content=HTML)
            with mock.patch.object(fetch_stage.HttpClient, "get_binary", return_value=resp):
                fetch_stage.run_fetch(tmp, run_cfg)
            # the fetch output is consumable by build-corpus end-to-end
            result = corpus.build_corpus(tmp, "run-fetch")
        self.assertGreaterEqual(result["unique_documents"], 1)
        self.assertGreaterEqual(result["query_hits"], 2)


if __name__ == "__main__":
    unittest.main()
