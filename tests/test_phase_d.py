from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from crop_search_framework import corpus, llm_extract as lx


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _fake_repo(tmp: Path, raw_text: str) -> None:
    _write(tmp / "config/parameters/core-crop-parameters.json", {
        "manifest_version": "0.4.0",
        "parameters": [{"parameter_id": "temperature.base_temperature", "label": "Base",
                        "family": "temperature", "implementation_status": "active"}],
    })
    raw = tmp / "exploration/raw/run-x"
    _write(raw / "summary.json", {"crop": "wheat", "failure_count": 0})
    _write(raw / "run-x-capture-001.json", {
        "id": "run-x-capture-001", "parameter_id": "temperature.base_temperature",
        "parameter_family": "temperature", "source_tier_id": "peer_reviewed_science",
        "source_url": "https://a.example/1", "final_url": "https://a.example/1",
        "source_title": "Doc A", "source_domain": "a.example", "discovery_method": "openalex",
        "access_status": "open_full_text", "document_type": "html", "query": "wheat base temperature",
        "raw_text": raw_text, "source_metadata": {"doi": "10.1/a"},
    })


class ManifestImmutabilityTests(unittest.TestCase):
    def test_manifest_carries_fingerprint_and_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _fake_repo(tmp, "Wheat base temperature is about 0 C across studies. " * 6)
            corpus.build_corpus(tmp, "run-x")
            m1 = json.loads((tmp / "exploration/corpus/run-x/corpus_manifest.json").read_text())
            self.assertTrue(m1["corpus_content_hash"])
            self.assertEqual(m1["manifest_revision"], 1)
            self.assertIn("parameter_manifest", m1["policy_hashes"])

            # Rebuild with identical content -> same fingerprint, no revision bump.
            corpus.build_corpus(tmp, "run-x")
            m2 = json.loads((tmp / "exploration/corpus/run-x/corpus_manifest.json").read_text())
            self.assertEqual(m2["corpus_content_hash"], m1["corpus_content_hash"])
            self.assertEqual(m2["manifest_revision"], 1)

    def test_changed_content_bumps_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _fake_repo(tmp, "Wheat base temperature is about 0 C across studies. " * 6)
            corpus.build_corpus(tmp, "run-x")
            m1 = json.loads((tmp / "exploration/corpus/run-x/corpus_manifest.json").read_text())
            # Change the source text -> new document hash -> revision bump.
            _fake_repo(tmp, "Wheat base temperature is near 2 C in revised studies. " * 6)
            corpus.build_corpus(tmp, "run-x")
            m2 = json.loads((tmp / "exploration/corpus/run-x/corpus_manifest.json").read_text())
            self.assertNotEqual(m2["corpus_content_hash"], m1["corpus_content_hash"])
            self.assertEqual(m2["manifest_revision"], 2)
            self.assertEqual(m2["previous_corpus_content_hash"], m1["corpus_content_hash"])


class BlockFedInputTests(unittest.TestCase):
    def test_blocks_preferred_and_tables_rendered_with_anchors(self):
        capture = {
            "candidate_claims": ["legacy candidate that must NOT be used"],
            "document_blocks": {
                "sections": [{"heading": "Nutrients", "anchor": "doc-1-sec-1"}],
                "paragraphs": [{"text": "Apply nitrogen in spring.", "anchor": "doc-1-p-3"}],
                "tables": [{
                    "anchor": "doc-1-table-2", "caption": "N rate by yield goal",
                    "header": ["Yield", "N rate"], "rows": [["60 bu", "90 kg/ha"]],
                }],
            },
        }
        body = lx.capture_input_text(capture)
        self.assertIn("doc-1-table-2", body)
        self.assertIn("90 kg/ha", body)
        self.assertIn("doc-1-p-3", body)
        self.assertNotIn("legacy candidate", body)

    def test_falls_back_to_candidate_claims_without_blocks(self):
        body = lx.capture_input_text({"candidate_claims": ["base temperature 0 C"]})
        self.assertIn("base temperature 0 C", body)


if __name__ == "__main__":
    unittest.main()
