from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from crop_search_framework import vault_render as vr


def make_claim(crop, pid, value, prov, confidence="high", claim_text="evidence here", **extra):
    c = {
        "entity": {"entity_type": "crop", "name": crop},
        "parameter_id": pid,
        "value": value,
        "provenance": prov,
        "confidence": confidence,
        "claim_text": claim_text,
    }
    c.update(extra)
    return c


META = {"label": "Base temperature", "domain": "temperature", "family": "temperature", "parameter_kind": "trait"}


class HelperTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(vr.slugify("Optimum Growth Temperature"), "optimum-growth-temperature")
        self.assertEqual(vr.slugify("N — rate (kg/ha)"), "n-rate-kg-ha")

    def test_summarize_numeric_range(self):
        claims = [
            make_claim("wheat", "temperature.base_temperature",
                       {"value_type": "numeric", "numeric_value": 0, "unit": "celsius"}, {}),
            make_claim("wheat", "temperature.base_temperature",
                       {"value_type": "range", "range_min": 2, "range_max": 4, "unit": "celsius"}, {}),
        ]
        out = vr.summarize_values(claims)
        self.assertEqual(out["summary"], "0–4 °C")
        self.assertEqual(out["numeric_count"], 2)

    def test_summarize_text_only(self):
        claims = [make_claim("wheat", "x", {"value_type": "text", "text_value": "warm"}, {})]
        self.assertIn("qualitative", vr.summarize_values(claims)["summary"])

    def test_best_confidence(self):
        claims = [make_claim("w", "x", {}, {}, confidence="low"),
                  make_claim("w", "x", {}, {}, confidence="high")]
        self.assertEqual(vr.best_confidence(claims), "high")


class DataPointNoteTests(unittest.TestCase):
    def test_note_has_marker_tags_and_links(self):
        claims = [make_claim(
            "wheat", "temperature.base_temperature",
            {"value_type": "numeric", "numeric_value": 0, "unit": "celsius", "qualifier": "threshold"},
            {"source_urls": ["https://fao.org/x"], "source_title": "FAO Wheat", "source_domain": "fao.org",
             "source_tier_id": "international_institution"},
            methods=["sowing"], organisms=[{"name": "Septoria", "role": "disease"}],
        )]
        fn, content = vr.render_data_point_note("wheat", "temperature.base_temperature", claims, META, "0.3.0", "run-x", "2026-06-23")
        self.assertEqual(fn, "Wheat — Base temperature.md")
        self.assertIn("generated_by: crop-search", content)
        self.assertIn("crop/wheat", content)
        self.assertIn("param/base-temperature", content)
        self.assertIn("source-tier/institution", content)
        self.assertIn("method/sowing", content)
        self.assertIn("pest/septoria", content)
        self.assertIn("[[Wheat]]", content)
        self.assertIn("[[Base temperature]]", content)
        self.assertIn("[[Septoria]]", content)
        self.assertIn("0 °C", content)


class RenderVaultTests(unittest.TestCase):
    def _repo(self, tmp: Path):
        (tmp / "config/parameters").mkdir(parents=True)
        (tmp / "config/parameters/core-crop-parameters.json").write_text(json.dumps({
            "manifest_version": "0.3.0",
            "parameters": [{"parameter_id": "temperature.base_temperature", "label": "Base temperature",
                            "family": "temperature", "domain": "temperature", "parameter_kind": "trait"}],
        }))
        cdir = tmp / "exploration/normalized/run-x"
        cdir.mkdir(parents=True)
        claim = make_claim("wheat", "temperature.base_temperature",
                           {"value_type": "numeric", "numeric_value": 0, "unit": "celsius"},
                           {"source_urls": ["https://fao.org/x"], "source_title": "FAO Wheat",
                            "source_domain": "fao.org", "source_tier_id": "international_institution"})
        (cdir / "run-x-capture-001-claim-001.json").write_text(json.dumps(claim))

    def test_writes_notes_and_respects_dry_run(self):
        with tempfile.TemporaryDirectory() as t:
            repo, vault = Path(t) / "repo", Path(t) / "vault"
            repo.mkdir()
            self._repo(repo)
            dry = vr.render_vault(repo, "run-x", vault, "sub", dry_run=True)
            self.assertEqual(dry["data_point_notes"], 1)
            self.assertFalse((vault / "sub").exists())  # nothing written
            wet = vr.render_vault(repo, "run-x", vault, "sub", dry_run=False)
            self.assertGreater(wet["files_written"], 0)
            self.assertTrue((vault / "sub" / "Wheat — Base temperature.md").exists())

    def test_never_overwrites_foreign_files(self):
        with tempfile.TemporaryDirectory() as t:
            repo, vault = Path(t) / "repo", Path(t) / "vault"
            repo.mkdir()
            self._repo(repo)
            note = vault / "sub" / "Wheat — Base temperature.md"
            note.parent.mkdir(parents=True)
            note.write_text("# My own hand-written note, not generated\n")
            summary = vr.render_vault(repo, "run-x", vault, "sub", dry_run=False)
            self.assertGreaterEqual(summary["files_skipped_foreign"], 1)
            self.assertIn("hand-written", note.read_text())  # untouched


if __name__ == "__main__":
    unittest.main()
