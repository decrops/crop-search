from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from crop_search_framework import eval_harness


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


class ValueMatchTests(unittest.TestCase):
    def test_numeric_within_tolerance(self):
        self.assertTrue(eval_harness.values_match(
            {"value_type": "numeric", "numeric_value": 15.2},
            {"value_type": "numeric", "numeric_value": 15},
        ))

    def test_numeric_outside_tolerance(self):
        self.assertFalse(eval_harness.values_match(
            {"value_type": "numeric", "numeric_value": 25},
            {"value_type": "numeric", "numeric_value": 15},
        ))

    def test_point_inside_predicted_range_matches_numeric_gold(self):
        self.assertTrue(eval_harness.values_match(
            {"value_type": "range", "range_min": 1.0, "range_max": 1.15},
            {"value_type": "numeric", "numeric_value": 1.1},
        ))

    def test_range_overlap(self):
        self.assertTrue(eval_harness.values_match(
            {"value_type": "range", "range_min": 60, "range_max": 70},
            {"value_type": "range", "range_min": 68, "range_max": 77},
        ))
        self.assertFalse(eval_harness.values_match(
            {"value_type": "range", "range_min": 10, "range_max": 20},
            {"value_type": "range", "range_min": 68, "range_max": 77},
        ))

    def test_unit_and_evidence(self):
        gold = {"value_type": "numeric", "numeric_value": 15, "unit": "percent", "evidence_contains": "15%"}
        pred = {"numeric_value": 15, "unit": "percent", "evidence_text": "ready at 15% moisture"}
        self.assertTrue(eval_harness.unit_matches(pred, gold))
        self.assertTrue(eval_harness.evidence_is_faithful(pred, gold))
        self.assertFalse(eval_harness.evidence_is_faithful({"evidence_text": "no number here"}, gold))


class ExtractionEvalTests(unittest.TestCase):
    def _repo(self, tmp: Path) -> None:
        _write(tmp / "tests/golden/extraction/harvest.json", {
            "domain": "harvest",
            "records": [{
                "document_id": "doc-1", "parameter_id": "harvest.harvest_moisture",
                "value_type": "numeric", "numeric_value": 15, "unit": "percent",
                "evidence_contains": "15%",
            }],
        })
        # doc-1: one correct moisture claim, one wrong-value moisture claim (adjudicable miss),
        # plus an unrelated claim that must NOT hurt precision (sparse gold).
        _write(tmp / "exploration/llm_cache/run-1/doc-1.json", {"claims": [
            {"parameter_id": "harvest.harvest_moisture", "value_type": "numeric",
             "numeric_value": 15, "unit": "percent", "evidence_text": "harvest at 15% moisture"},
            {"parameter_id": "harvest.harvest_moisture", "value_type": "numeric",
             "numeric_value": 40, "unit": "percent", "evidence_text": "bogus 40%"},
            {"parameter_id": "temperature.optimum_growth_temperature", "value_type": "numeric",
             "numeric_value": 20, "unit": "celsius", "evidence_text": "20 C"},
        ]})

    def test_extraction_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            report = eval_harness.eval_extraction(root, "run-1")
            self.assertEqual(report["gold_records"], 1)
            # recall: the gold fact is found.
            self.assertEqual(report["metrics"]["recall"], 1.0)
            # adjudicable precision: 2 moisture claims, 1 correct -> 0.5. The
            # unrelated temperature claim is NOT adjudicable and does not count.
            self.assertEqual(report["adjudicable_predictions"], 2)
            self.assertEqual(report["metrics"]["precision"], 0.5)
            self.assertEqual(report["metrics"]["unit_normalization_correctness"], 1.0)
            # scorecard written
            self.assertTrue((root / "exploration/eval/run-1/scorecard-extraction.json").exists())


class RetrievalEvalTests(unittest.TestCase):
    def _repo(self, tmp: Path, with_ledger: bool) -> None:
        _write(tmp / "tests/golden/retrieval/water.json", {
            "domain": "water",
            "records": [{
                "parameter_id": "water.crop_coefficient",
                "expected_domain": "fao.org",
                "expected_url": "https://www.fao.org/wheat",
                "doi": "",
            }],
        })
        if with_ledger:
            ledger = tmp / "exploration/discovery/run-1/results.jsonl"
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_text("\n".join([
                json.dumps({"source_url": "https://www.fao.org/wheat", "canonical_key": "fao",
                            "discovery_rank": 2, "doi": ""}),
                json.dumps({"source_url": "https://example.com/x", "canonical_key": "x",
                            "discovery_rank": 1, "doi": ""}),
            ]) + "\n", encoding="utf-8")
            queue = tmp / "exploration/discovery/run-1/fetch_queue.jsonl"
            queue.write_text(json.dumps(
                {"canonical_key": "fao", "fetch_selected": True}) + "\n", encoding="utf-8")

    def test_no_ledger_reports_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root, with_ledger=False)
            report = eval_harness.eval_retrieval(root, "run-1")
        self.assertFalse(report["ledger_available"])
        self.assertIn("note", report)
        self.assertEqual(report["metrics"]["source_recall"], 0.0)

    def test_ledger_recall_and_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root, with_ledger=True)
            report = eval_harness.eval_retrieval(root, "run-1")
        self.assertTrue(report["ledger_available"])
        self.assertEqual(report["metrics"]["source_recall"], 1.0)
        self.assertEqual(report["metrics"]["fetch_selection_recall"], 1.0)
        self.assertEqual(report["metrics"]["mean_rank"], 2)


if __name__ == "__main__":
    unittest.main()
