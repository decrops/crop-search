import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from crop_search_framework import llm_extract as lx


REPO = Path(__file__).resolve().parents[1]


def load_manifest():
    return json.loads((REPO / "config/parameters/core-crop-parameters.json").read_text())


class OutputSchemaTests(unittest.TestCase):
    def test_enum_constrains_to_active_ids_plus_none(self):
        manifest = load_manifest()
        active = lx.active_parameter_ids(manifest)
        schema = lx.build_output_schema(active)
        pid_enum = schema["properties"]["claims"]["items"]["properties"]["parameter_id"]["enum"]
        self.assertIn("temperature.base_temperature", pid_enum)
        self.assertIn("none", pid_enum)
        # stubs must NOT be valid extraction targets
        self.assertNotIn("crop_protection.key_pest_threshold", pid_enum)

    def test_schema_is_strict(self):
        schema = lx.build_output_schema(["temperature.base_temperature"])
        item = schema["properties"]["claims"]["items"]
        self.assertFalse(item["additionalProperties"])
        # Required = the core keys; optional extension keys (block provenance,
        # organisms, economics) are valid properties but not required so older
        # cached extractions stay valid.
        self.assertEqual(set(item["required"]), set(lx.REQUIRED_EXTRACTION_KEYS))
        for key in lx.OPTIONAL_EXTRACTION_KEYS:
            self.assertIn(key, item["properties"])

    def test_active_excludes_stubs(self):
        manifest = load_manifest()
        ids = lx.active_parameter_ids(manifest)
        self.assertNotIn("economics.input_intensity", ids)
        self.assertGreaterEqual(len(ids), 80)


class FixtureBackendTests(unittest.TestCase):
    def setUp(self):
        self.params = lx.active_parameters(load_manifest())

    def test_stub_extracts_and_parses_temperature_range(self):
        capture = {
            "id": "cap-1",
            "candidate_claims": [
                "The optimum temperature for wheat is 20 to 25 C under irrigated conditions.",
                "Recommended planting date is mid October.",
                "This sentence is generic narrative with no parameter.",
            ],
        }
        claims = lx.FixtureBackend().extract(capture, "wheat", self.params)
        by_pid = {c["parameter_id"]: c for c in claims}
        self.assertIn("temperature.optimum_growth_temperature", by_pid)
        self.assertIn("planting.planting_window", by_pid)
        temp = by_pid["temperature.optimum_growth_temperature"]
        self.assertEqual(temp["value_type"], "range")
        self.assertEqual(temp["range_min"], 20.0)
        self.assertEqual(temp["range_max"], 25.0)
        self.assertEqual(temp["unit"], "celsius")
        self.assertEqual(temp["management_system"], "irrigated")
        # every claim carries the full key set
        for c in claims:
            self.assertEqual(set(c.keys()), set(lx.EXTRACTION_KEYS))

    def test_deterministic(self):
        capture = {"id": "c", "candidate_claims": ["base temperature is 0 C"]}
        a = lx.FixtureBackend().extract(capture, "wheat", self.params)
        b = lx.FixtureBackend().extract(capture, "wheat", self.params)
        self.assertEqual(a, b)

    def test_replay_from_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "cap-x.json").write_text(json.dumps({
                "claims": [{
                    "parameter_id": "soil.ph_range", "value_type": "range",
                    "range_min": 6.0, "range_max": 7.0, "unit": "pH",
                    "qualifier": "recommended", "evidence_text": "pH 6.0 to 7.0",
                    "claim_summary": "pH 6-7", "extraction_confidence": "high",
                }]
            }))
            capture = {"id": "cap-x", "candidate_claims": ["irrelevant to stub"]}
            claims = lx.FixtureBackend(cache_dir=cache).extract(capture, "wheat", self.params)
            self.assertEqual(len(claims), 1)
            self.assertEqual(claims[0]["parameter_id"], "soil.ph_range")
            self.assertEqual(claims[0]["range_max"], 7.0)


class ValidationTests(unittest.TestCase):
    def test_drops_none_and_unknown_and_dedupes(self):
        active = ["temperature.base_temperature"]
        raw = [
            {"parameter_id": "none", "evidence_text": "x"},
            {"parameter_id": "made.up", "evidence_text": "y"},
            {"parameter_id": "temperature.base_temperature", "evidence_text": "base temp 0C"},
            {"parameter_id": "temperature.base_temperature", "evidence_text": "base temp 0C"},
            {"parameter_id": "temperature.base_temperature", "evidence_text": ""},
        ]
        out = lx.validate_extraction_claims(raw, active)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["parameter_id"], "temperature.base_temperature")
        for key in lx.OPTIONAL_EXTRACTION_KEYS:
            self.assertIsNone(out[0][key])

    def test_preserves_optional_extension_fields(self):
        active = ["temperature.base_temperature"]
        raw = [{
            "parameter_id": "temperature.base_temperature",
            "value_type": "numeric",
            "numeric_value": 0,
            "unit": "celsius",
            "qualifier": "threshold",
            "evidence_text": "base temp 0 C",
            "claim_summary": "base temp",
            "extraction_confidence": "high",
            "organisms": [{"name": "stripe rust", "role": "disease"}],
            "method": "extension guide",
            "document_id": "doc-1",
            "block_anchor": "doc-1-table-2",
            "block_type": "table",
            "page": 4,
            "table_label": "Table 2",
        }]
        out = lx.validate_extraction_claims(raw, active)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["organisms"], [{"name": "stripe rust", "role": "disease"}])
        self.assertEqual(out[0]["block_anchor"], "doc-1-table-2")
        self.assertEqual(out[0]["page"], 4)


class RunDriverTests(unittest.TestCase):
    def test_extract_run_writes_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config/parameters").mkdir(parents=True)
            (root / "exploration/raw/run-1").mkdir(parents=True)
            (root / "config/parameters/core-crop-parameters.json").write_text(json.dumps({
                "manifest_version": "0.3.0", "scope": "test",
                "parameters": [{
                    "parameter_id": "temperature.base_temperature", "label": "Base temperature",
                    "family": "temperature", "category": "physiological_parameter",
                    "value_type": "numeric_or_range", "normalized_attribute": "temperature_requirement",
                    "normalized_attribute_subtype": "base_temperature",
                    "search_aliases": ["base temperature"], "evidence_patterns": ["{crop} base temperature"],
                    "review_policy": {"allow_canonical": False, "conflict_key": ["crop"], "merge_if_values_overlap": True},
                    "implementation_status": "active",
                }],
            }))
            (root / "exploration/raw/run-1/summary.json").write_text(json.dumps({"crop": "wheat"}))
            (root / "exploration/raw/run-1/run-1-capture-001.json").write_text(json.dumps({
                "id": "run-1-capture-001",
                "candidate_claims": ["The base temperature for wheat is about 0 C."],
            }))
            summary = lx.extract_run(root, "run-1", lx.FixtureBackend())
            self.assertEqual(summary["captures"], 1)
            self.assertEqual(summary["extracted_claims"], 1)
            self.assertEqual(summary["parameters_with_claims"], 1)
            out = root / "exploration/llm_extractions/run-1"
            self.assertTrue((out / "summary.json").exists())
            self.assertTrue((out / "run-1-capture-001.json").exists())


class LocalBackendTests(unittest.TestCase):
    def setUp(self):
        self.params = lx.active_parameters(load_manifest())
        self.capture = {
            "id": "cap-1", "candidate_claims": ["base temperature is around 0 C"],
            "source_title": "Wheat guide", "source_domain": "ex", "source_tier_label": "",
        }

    def _ollama_reply(self, claims):
        resp = mock.Mock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"message": {"content": json.dumps({"claims": claims})}}
        return resp

    def test_parses_ollama_structured_response(self):
        reply = self._ollama_reply([
            {"parameter_id": "temperature.base_temperature",
             "evidence_text": "base temperature is around 0 C",
             "value_type": "numeric", "numeric_value": 0, "unit": "celsius",
             "qualifier": "threshold", "claim_summary": "base temp 0C",
             "extraction_confidence": "medium"},
            {"parameter_id": "none", "evidence_text": "drop me"},
        ])
        with mock.patch("requests.post", return_value=reply) as posted:
            claims = lx.LocalBackend(model="llama3.1").extract(self.capture, "wheat", self.params)
        self.assertEqual(len(claims), 1)  # 'none' dropped
        self.assertEqual(claims[0]["parameter_id"], "temperature.base_temperature")
        # posted to /api/chat with the enum-constrained schema as `format`
        _, kwargs = posted.call_args
        self.assertTrue(posted.call_args[0][0].endswith("/api/chat"))
        fmt = kwargs["json"]["format"]
        self.assertIn("temperature.base_temperature",
                      fmt["properties"]["claims"]["items"]["properties"]["parameter_id"]["enum"])
        self.assertEqual(kwargs["json"]["options"]["temperature"], 0)

    def test_connection_error_is_actionable(self):
        with mock.patch("requests.post", side_effect=requests.exceptions.ConnectionError()):
            with self.assertRaises(RuntimeError) as ctx:
                lx.LocalBackend().extract(self.capture, "wheat", self.params)
        self.assertIn("ollama", str(ctx.exception).lower())


class BackendFactoryTests(unittest.TestCase):
    def test_llm_backend_constructs_without_importing_anthropic(self):
        # constructing must not require anthropic (import is lazy in extract())
        backend = lx.make_backend("llm")
        self.assertEqual(backend.name, "llm")

    def test_local_backend_defaults_to_llama_but_respects_override(self):
        self.assertEqual(lx.make_backend("local").model, "llama3.1")
        self.assertEqual(lx.make_backend("local", model="qwen2.5").model, "qwen2.5")

    def test_unknown_backend_raises(self):
        with self.assertRaises(ValueError):
            lx.make_backend("nope")


if __name__ == "__main__":
    unittest.main()
