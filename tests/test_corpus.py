from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from crop_search_framework import corpus


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def build_fake_repo(tmp: Path) -> None:
    _write(tmp / "config/parameters/core-crop-parameters.json", {
        "manifest_version": "0.3.0",
        "parameters": [
            {"parameter_id": "temperature.base_temperature", "label": "Base temperature",
             "family": "temperature", "implementation_status": "active"},
            {"parameter_id": "nutrients.nitrogen_requirement", "label": "Nitrogen requirement",
             "family": "nutrients", "implementation_status": "active"},
        ],
    })
    raw = tmp / "exploration/raw/run-x"
    _write(raw / "summary.json", {"crop": "wheat", "failure_count": 7})
    # two captures share identical text -> one document; a third is distinct
    shared = "Wheat base temperature is close to 0 degrees C across studies. " * 5
    _write(raw / "run-x-capture-001.json", {
        "id": "run-x-capture-001", "parameter_id": "temperature.base_temperature",
        "parameter_family": "temperature", "source_tier_id": "peer_reviewed_science",
        "source_url": "https://a.example/1", "final_url": "https://a.example/1",
        "source_title": "Doc A", "source_domain": "a.example", "discovery_method": "openalex",
        "access_status": "open_full_text", "document_type": "html", "query": "wheat base temperature",
        "raw_text": shared, "source_metadata": {"doi": "10.1/a"},
    })
    _write(raw / "run-x-capture-002.json", {
        "id": "run-x-capture-002", "parameter_id": "nutrients.nitrogen_requirement",
        "parameter_family": "nutrients", "source_tier_id": "peer_reviewed_science",
        "source_url": "https://a.example/1", "final_url": "https://a.example/1",
        "source_title": "Doc A", "source_domain": "a.example", "discovery_method": "openalex",
        "access_status": "open_full_text", "document_type": "html", "query": "wheat nitrogen",
        "raw_text": shared, "source_metadata": {"doi": "10.1/a"},
    })
    _write(raw / "run-x-capture-003.json", {
        "id": "run-x-capture-003", "parameter_id": "nutrients.nitrogen_requirement",
        "parameter_family": "nutrients", "source_tier_id": "extension_publication",
        "source_url": "https://b.example/2", "final_url": "https://b.example/2",
        "source_title": "Doc B", "source_domain": "en.wikipedia.org", "discovery_method": "wikipedia",
        "access_status": "metadata_only", "document_type": "html", "query": "wheat nitrogen",
        "raw_text": "", "source_metadata": {},
    })


class BuildCorpusTests(unittest.TestCase):
    def test_dedupes_identical_text_into_one_document(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            build_fake_repo(repo)
            summary = corpus.build_corpus(repo, "run-x")
            self.assertEqual(summary["captures"], 3)
            # two identical-text captures collapse to one doc; the empty-text one is its own doc
            self.assertEqual(summary["unique_documents"], 2)
            self.assertEqual(summary["query_hits"], 3)
            out = repo / "exploration/corpus/run-x"
            self.assertTrue((out / "corpus_manifest.json").exists())
            self.assertTrue((out / "query_hits.jsonl").exists())
            self.assertEqual(len(list((out / "documents").glob("doc-*.json"))), 2)
            self.assertEqual(len(list((out / "blocks").glob("doc-*.json"))), 2)

    def test_query_hits_link_both_params_to_shared_doc(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            build_fake_repo(repo)
            corpus.build_corpus(repo, "run-x")
            hits = [json.loads(line) for line in
                    (repo / "exploration/corpus/run-x/query_hits.jsonl").read_text().splitlines() if line]
            shared = [h for h in hits if h["capture_id"] in ("run-x-capture-001", "run-x-capture-002")]
            # both captures (identical text) point at the SAME document...
            self.assertEqual(len({h["document_id"] for h in shared}), 1)
            # ...and that one document carries both parameter associations
            self.assertEqual(
                {h["parameter_id"] for h in shared},
                {"temperature.base_temperature", "nutrients.nitrogen_requirement"},
            )


class CorpusQaTests(unittest.TestCase):
    def test_qa_metrics_and_gates(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            build_fake_repo(repo)
            corpus.build_corpus(repo, "run-x")
            report = corpus.corpus_qa(repo, "run-x")
            self.assertEqual(report["capture_count"], 3)
            self.assertEqual(report["unique_document_count"], 2)
            self.assertEqual(report["documents_with_text"], 1)
            self.assertEqual(report["metadata_only_count"], 1)
            # duplicate text ratio within the Opus input set is 0 (each doc unique text)
            self.assertEqual(report["duplicate_text_ratio"], 0.0)
            self.assertIn("gates", report)
            self.assertIn("gates_passed", report)
            self.assertTrue((repo / "exploration/corpus/run-x/qa_report.md").exists())


class HashingTests(unittest.TestCase):
    def test_text_hash_is_whitespace_insensitive(self) -> None:
        self.assertEqual(corpus.text_hash("a  b\n c"), corpus.text_hash("a b c"))

    def test_empty_text_hashes_to_empty(self) -> None:
        self.assertEqual(corpus.text_hash("   "), "")


if __name__ == "__main__":
    unittest.main()
