from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crop_search_framework import discovery
from crop_search_framework.parameters import QueryPlanItem

REPO = Path(__file__).resolve().parents[1]


class CanonicalKeyTests(unittest.TestCase):
    def test_doi_preferred_and_normalized(self):
        self.assertEqual(
            discovery.canonical_key("https://x.org/a", "https://doi.org/10.1/AbC"),
            "doi:10.1/abc",
        )

    def test_url_normalized_when_no_doi(self):
        a = discovery.canonical_key("https://www.FAO.org/wheat/", "")
        b = discovery.canonical_key("http://fao.org/wheat", "")
        self.assertEqual(a, b)
        self.assertEqual(a, "url:fao.org/wheat")


class DiscoveryExecuteTests(unittest.TestCase):
    def _tmp_repo(self, tmp: Path) -> Path:
        shutil.copytree(REPO / "schemas", tmp / "schemas")
        shutil.copy(REPO / "config/runs/pilot-global-wheat.json", tmp / "run.json")
        return tmp / "run.json"

    def _rows(self):
        return [
            {  # high-score scholarly row (kept)
                "title": "Wheat N study", "source_url": "https://j.org/a",
                "source_domain": "j.org", "score": 18, "score_components": {"token_overlap": 5},
                "discovery_method": "openalex", "access_status": "open_full_text",
                "source_metadata": {"doi": "10.1/a"},
            },
            {  # duplicate of the first by DOI (lower score) -> stamped duplicate
                "title": "Wheat N study (dup)", "source_url": "https://doi.org/10.1/a",
                "source_domain": "doi.org", "score": 9, "score_components": {},
                "discovery_method": "crossref", "access_status": "metadata_only",
                "source_metadata": {"doi": "10.1/a"},
            },
            {  # low-score wikipedia row -> stamped relevance_gate (still kept)
                "title": "Wheat", "source_url": "https://en.wikipedia.org/wiki/Wheat",
                "source_domain": "en.wikipedia.org", "score": 1, "score_components": {},
                "discovery_method": "wikipedia", "access_status": "open_full_text",
                "source_metadata": {},
            },
        ]

    def test_ledger_is_complete_and_stamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            run_config_path = self._tmp_repo(tmp)
            plan = [QueryPlanItem(
                query="wheat nitrogen kg/ha", parameter_id="nutrients.nitrogen_requirement",
                parameter_family="nutrients", parameter_label="N", source_tier_id="peer_reviewed_science",
                source_tier_label="Peer reviewed",
            )]
            with mock.patch.object(discovery, "query_plan_for_run", return_value=plan), \
                 mock.patch.object(discovery, "connector_results_for_tier", return_value=(self._rows(), ["openalex: 429"])), \
                 mock.patch.object(discovery, "configure_client"):
                summary = discovery.discover(tmp, run_config_path)

            ledger = [json.loads(l) for l in (tmp / "exploration/discovery/pilot-global-wheat-001/results.jsonl").read_text().splitlines()]

        # All 3 raw rows are recorded — nothing dropped from the ledger.
        self.assertEqual(len(ledger), 3)
        self.assertEqual(summary["ledger_rows"], 3)
        self.assertEqual(summary["unique_sources"], 2)  # two share a DOI
        reasons = {r["title"]: r["discovery_drop_reason"] for r in ledger}
        self.assertEqual(reasons["Wheat N study"], "")          # best of the dup pair: kept
        self.assertEqual(reasons["Wheat N study (dup)"], "duplicate")
        self.assertEqual(reasons["Wheat"], "relevance_gate")
        # score components captured for the ledger
        kept = next(r for r in ledger if r["title"] == "Wheat N study")
        self.assertEqual(kept["score_components"], {"token_overlap": 5})
        self.assertEqual(kept["canonical_key"], "doi:10.1/a")
        # provider failure recorded to the retry queue
        self.assertEqual(summary["retry_queue_size"], 1)


if __name__ == "__main__":
    unittest.main()
