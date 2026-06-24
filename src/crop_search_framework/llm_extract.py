"""LLM-based claim extraction (Phase 2 core).

Maps a captured source's text to the *active* parameter manifest, returning typed,
provenance-ready claims. Two interchangeable backends:

- ``FixtureBackend``  — deterministic, no network. Replays a recorded model
  response from a cache dir when present, else falls back to a small keyword stub.
  This is what CI and offline development use; it keeps the pipeline exercisable
  without an API key or token spend.
- ``ClaudeBackend``   — live extraction via the Anthropic SDK (``claude-opus-4-8``),
  using structured outputs (the parameter id is enum-constrained to the active
  manifest) and a prompt-cached manifest table. ``anthropic`` is imported lazily,
  so this module imports fine without it installed.

The output of either backend is a list of validated *extraction dicts* (see
``EXTRACTION_KEYS``). Turning those into normalized claims — attaching geo scopes,
``agronomic_scope``, ``bbch_applicability`` and ``provenance.manifest_version`` —
is the normalizer's job (next Phase 2 step) and follows ``.planning/extraction-contract.md``.
"""
from __future__ import annotations

import abc
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_LOCAL_MODEL = "llama3.1"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"

VALUE_TYPES = ("numeric", "range", "text")
QUALIFIERS = ("optimal", "threshold", "recommended", "risk", "descriptive")
CONFIDENCES = ("high", "medium", "low")

# Core keys that every extraction output must carry.
REQUIRED_EXTRACTION_KEYS = (
    "parameter_id",
    "value_type",
    "numeric_value",
    "range_min",
    "range_max",
    "unit",
    "qualifier",
    "evidence_text",
    "claim_summary",
    "extraction_confidence",
    "cultivar",
    "management_system",
    "bbch_min",
    "bbch_max",
)

# Optional extension keys. These are accepted from newer extraction backends and
# backfilled to None for older cached outputs.
OPTIONAL_EXTRACTION_KEYS = (
    "organisms",
    "method",
    "price_year",
    "currency",
    "area_unit",
    "document_id",
    "block_anchor",
    "block_type",
    "page",
    "table_label",
)

# Canonical key order for an internal extraction dict.
EXTRACTION_KEYS = REQUIRED_EXTRACTION_KEYS + OPTIONAL_EXTRACTION_KEYS

# Cap how much source text we hand a single extraction call.
MAX_INPUT_CLAIMS = 60
MAX_INPUT_CHARS = 12000


# --------------------------------------------------------------------------- #
# Manifest helpers
# --------------------------------------------------------------------------- #
def active_parameters(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        p
        for p in manifest.get("parameters", [])
        if p.get("implementation_status", "active") == "active"
    ]


def active_parameter_ids(manifest: Dict[str, Any]) -> List[str]:
    return [p["parameter_id"] for p in active_parameters(manifest)]


def build_output_schema(active_ids: List[str]) -> Dict[str, Any]:
    """JSON schema for ``output_config.format`` — strict, enum-constrained."""
    nullable_number = {"type": ["number", "null"]}
    nullable_int = {"type": ["integer", "null"], "minimum": 0, "maximum": 99}
    nullable_page = {"type": ["integer", "null"], "minimum": 1}
    nullable_string = {"type": ["string", "null"]}
    nullable_year = {"type": ["integer", "null"], "minimum": 1900, "maximum": 2100}
    organism = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "role": nullable_string,
        },
    }
    claim = {
        "type": "object",
        "additionalProperties": False,
        "required": list(REQUIRED_EXTRACTION_KEYS),
        "properties": {
            "parameter_id": {"type": "string", "enum": list(active_ids) + ["none"]},
            "value_type": {"type": "string", "enum": list(VALUE_TYPES)},
            "numeric_value": nullable_number,
            "range_min": nullable_number,
            "range_max": nullable_number,
            "unit": nullable_string,
            "qualifier": {"type": "string", "enum": list(QUALIFIERS)},
            "evidence_text": {"type": "string"},
            "claim_summary": {"type": "string"},
            "extraction_confidence": {"type": "string", "enum": list(CONFIDENCES)},
            "cultivar": nullable_string,
            "management_system": nullable_string,
            "bbch_min": nullable_int,
            "bbch_max": nullable_int,
            "organisms": {"type": ["array", "null"], "items": organism},
            "method": nullable_string,
            "price_year": nullable_year,
            "currency": nullable_string,
            "area_unit": nullable_string,
            "document_id": nullable_string,
            "block_anchor": nullable_string,
            "block_type": {"type": ["string", "null"], "enum": ["paragraph", "table", "heading", "section", None]},
            "page": nullable_page,
            "table_label": nullable_string,
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["claims"],
        "properties": {"claims": {"type": "array", "items": claim}},
    }


def build_manifest_table(params: List[Dict[str, Any]]) -> str:
    lines = ["parameter_id | label | family | value_type | aliases"]
    for p in params:
        aliases = ", ".join((p.get("search_aliases") or [])[:4])
        lines.append(
            "{0} | {1} | {2} | {3} | {4}".format(
                p["parameter_id"], p["label"], p["family"], p["value_type"], aliases
            )
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Prompt construction
# --------------------------------------------------------------------------- #
SYSTEM_INSTRUCTIONS = (
    "You extract structured agronomy claims from a single source document.\n"
    "For each attributable fact about the given crop, emit one claim mapped to the "
    "single best parameter_id from the manifest table. If a sentence is not an "
    "attributable parameter fact, do not emit a claim for it (do not pad with 'none').\n"
    "Rules:\n"
    "- parameter_id MUST be one of the manifest ids, or 'none' (which is dropped).\n"
    "- evidence_text MUST be a verbatim span from the source supporting the value.\n"
    "- Put a number/unit in numeric_value+unit (single) or range_min/range_max+unit (range); "
    "otherwise value_type='text' with a concise claim_summary.\n"
    "- Set management_system only if the text states one (e.g. irrigated, dryland, organic); "
    "set cultivar only if a named cultivar is given; else null.\n"
    "- Set bbch_min/bbch_max only if the value is tied to a growth stage in the text; else null.\n"
    "- Do not invent values not present in evidence_text."
)


def build_system_blocks(params: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    table = build_manifest_table(params)
    return [
        {"type": "text", "text": SYSTEM_INSTRUCTIONS},
        # Stable across every capture in a run -> cache it.
        {
            "type": "text",
            "text": "Manifest parameters (id | label | family | value_type | aliases):\n" + table,
            "cache_control": {"type": "ephemeral"},
        },
    ]


def capture_input_text(capture: Dict[str, Any]) -> str:
    """Build the extraction body.

    WS-7 part 2: prefer the corpus **block store** (sections/paragraphs/tables
    with anchors) when present, so table-bound values reach the model and each
    value can cite a block. Falls back to the legacy candidate_claims path for
    captures without blocks.
    """
    blocks = capture.get("document_blocks")
    if blocks:
        return render_blocks(blocks)
    claims = capture.get("candidate_claims") or capture.get("evidence_fragments") or []
    if not claims and capture.get("raw_text"):
        claims = [capture["raw_text"]]
    text = "\n".join(str(c) for c in claims[:MAX_INPUT_CLAIMS])
    return text[:MAX_INPUT_CHARS]


def render_blocks(blocks: Dict[str, Any]) -> str:
    """Render the block store to anchored text the model can cite by anchor.

    Tables are rendered row-by-row with their anchor/caption so numeric values
    that live only in tables (nutrients/harvest thresholds) are not lost.
    """
    parts: List[str] = []
    for section in blocks.get("sections", []):
        heading = section.get("heading")
        if heading:
            parts.append("## {0} [{1}]".format(heading, section.get("anchor", "")))
    for paragraph in blocks.get("paragraphs", []):
        anchor = paragraph.get("anchor", "")
        parts.append("[{0}] {1}".format(anchor, paragraph.get("text", "")))
    for table in blocks.get("tables", []):
        anchor = table.get("anchor", "")
        caption = table.get("caption", "")
        parts.append("[TABLE {0}] {1}".format(anchor, caption).rstrip())
        header = table.get("header") or []
        if header:
            parts.append("  | " + " | ".join(str(h) for h in header) + " |")
        for row in table.get("rows", []):
            parts.append("  | " + " | ".join(str(c) for c in row) + " |")
    text = "\n".join(parts)
    return text[:MAX_INPUT_CHARS]


def build_user_message(capture: Dict[str, Any], crop: str) -> str:
    return (
        "Crop: {crop}\n"
        "Source title: {title}\n"
        "Source domain: {domain}\n"
        "Source tier: {tier}\n"
        "---\n"
        "{body}".format(
            crop=crop,
            title=capture.get("source_title") or capture.get("search_title") or "",
            domain=capture.get("source_domain") or "",
            tier=capture.get("source_tier_label") or "",
            body=capture_input_text(capture),
        )
    )


# --------------------------------------------------------------------------- #
# Validation / normalization of raw model output
# --------------------------------------------------------------------------- #
def _coerce_organisms(value: Any) -> Optional[List[Dict[str, Optional[str]]]]:
    if not isinstance(value, list):
        return None
    organisms: List[Dict[str, Optional[str]]] = []
    for item in value:
        if isinstance(item, str):
            name = item.strip()
            if name:
                organisms.append({"name": name, "role": None})
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        role = item.get("role")
        organisms.append({"name": name, "role": str(role).strip() if role else None})
    return organisms or None


def _coerce_optional_int(value: Any, minimum: int, maximum: int = None) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def _coerce_claim(raw: Dict[str, Any], active_ids: set) -> Optional[Dict[str, Any]]:
    pid = raw.get("parameter_id")
    if pid not in active_ids:  # drops "none" and anything off-manifest
        return None
    evidence = (raw.get("evidence_text") or "").strip()
    if not evidence:
        return None
    value_type = raw.get("value_type") if raw.get("value_type") in VALUE_TYPES else "text"
    qualifier = raw.get("qualifier") if raw.get("qualifier") in QUALIFIERS else "descriptive"
    confidence = (
        raw.get("extraction_confidence")
        if raw.get("extraction_confidence") in CONFIDENCES
        else "low"
    )
    claim = {key: None for key in EXTRACTION_KEYS}
    claim.update(
        {
            "parameter_id": pid,
            "value_type": value_type,
            "numeric_value": raw.get("numeric_value"),
            "range_min": raw.get("range_min"),
            "range_max": raw.get("range_max"),
            "unit": raw.get("unit"),
            "qualifier": qualifier,
            "evidence_text": evidence,
            "claim_summary": (raw.get("claim_summary") or evidence)[:400],
            "extraction_confidence": confidence,
            "cultivar": raw.get("cultivar") or None,
            "management_system": raw.get("management_system") or None,
            "bbch_min": raw.get("bbch_min"),
            "bbch_max": raw.get("bbch_max"),
            "organisms": _coerce_organisms(raw.get("organisms")),
            "method": raw.get("method") or None,
            "price_year": _coerce_optional_int(raw.get("price_year"), 1900, 2100),
            "currency": raw.get("currency") or None,
            "area_unit": raw.get("area_unit") or None,
            "document_id": raw.get("document_id") or None,
            "block_anchor": raw.get("block_anchor") or None,
            "block_type": raw.get("block_type") if raw.get("block_type") in {"paragraph", "table", "heading", "section"} else None,
            "page": _coerce_optional_int(raw.get("page"), 1),
            "table_label": raw.get("table_label") or None,
        }
    )
    return claim


def validate_extraction_claims(
    raw_claims: List[Dict[str, Any]], active_ids: List[str]
) -> List[Dict[str, Any]]:
    """Drop off-manifest / 'none' claims and dedupe by (parameter_id, evidence_text)."""
    active = set(active_ids)
    seen = set()
    out: List[Dict[str, Any]] = []
    for raw in raw_claims:
        if not isinstance(raw, dict):
            continue
        claim = _coerce_claim(raw, active)
        if claim is None:
            continue
        key = (claim["parameter_id"], claim["evidence_text"])
        if key in seen:
            continue
        seen.add(key)
        out.append(claim)
    return out


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
class ExtractionBackend(abc.ABC):
    name = "abstract"

    @abc.abstractmethod
    def extract(
        self,
        capture: Dict[str, Any],
        crop: str,
        params: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Return validated extraction dicts for one capture."""


# Minimal deterministic keyword map for the offline stub (ids must exist as active).
_STUB_KEYWORDS = [
    ("base temperature", "temperature.base_temperature"),
    ("optimum", "temperature.optimum_growth_temperature"),
    ("germinat", "temperature.germination_temperature"),
    ("planting date", "planting.planting_window"),
    ("planting window", "planting.planting_window"),
    ("seeding rate", "planting.seeding_rate"),
    ("nitrogen", "nutrients.nitrogen_requirement"),
    ("ph ", "soil.ph_range"),
    ("evapotranspiration", "water.evapotranspiration_requirement"),
]
_TEMP_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:to|-|and)\s*(\d+(?:\.\d+)?)\s*(?:°|degrees?\s*)?([cf])", re.I)


class FixtureBackend(ExtractionBackend):
    """Deterministic backend: replay a recorded response, else a keyword stub."""

    name = "fixture"

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        self.cache_dir = cache_dir

    def _replay(self, capture_id: str) -> Optional[List[Dict[str, Any]]]:
        if not self.cache_dir:
            return None
        path = self.cache_dir / "{0}.json".format(capture_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("claims", [])

    def extract(self, capture, crop, params):
        active_ids = [p["parameter_id"] for p in params]
        replayed = self._replay(capture.get("id", ""))
        if replayed is not None:
            return validate_extraction_claims(replayed, active_ids)
        active = set(active_ids)
        raw: List[Dict[str, Any]] = []
        for sentence in (capture.get("candidate_claims") or [])[:MAX_INPUT_CLAIMS]:
            lowered = sentence.lower()
            pid = next((pid for kw, pid in _STUB_KEYWORDS if kw in lowered and pid in active), None)
            if not pid:
                continue
            claim = {"parameter_id": pid, "evidence_text": sentence,
                     "claim_summary": sentence, "qualifier": "descriptive",
                     "extraction_confidence": "low", "value_type": "text"}
            m = _TEMP_RANGE.search(sentence)
            if m and pid.startswith("temperature."):
                claim.update({"value_type": "range", "range_min": float(m.group(1)),
                              "range_max": float(m.group(2)),
                              "unit": "celsius" if m.group(3).lower() == "c" else "fahrenheit"})
            for sysword in ("irrigated", "dryland", "rainfed", "organic"):
                if sysword in lowered:
                    claim["management_system"] = sysword
                    break
            raw.append(claim)
        return validate_extraction_claims(raw, active_ids)


class ClaudeBackend(ExtractionBackend):
    """Live extraction via the Anthropic SDK. ``anthropic`` imported lazily."""

    name = "llm"

    def __init__(self, model: str = DEFAULT_MODEL, cache_dir: Optional[Path] = None,
                 max_tokens: int = 4096) -> None:
        self.model = model
        self.cache_dir = cache_dir
        self.max_tokens = max_tokens
        self._client = None

    def _client_or_raise(self):
        if self._client is None:
            try:
                import anthropic  # noqa: F401
            except ImportError as exc:  # pragma: no cover - depends on env
                raise RuntimeError(
                    "The 'anthropic' package is required for the llm backend. "
                    "Install it with: pip install -e .[llm]"
                ) from exc
            self._client = anthropic.Anthropic()
        return self._client

    def extract(self, capture, crop, params):
        active_ids = [p["parameter_id"] for p in params]
        client = self._client_or_raise()
        schema = build_output_schema(active_ids)
        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=build_system_blocks(params),
            messages=[{"role": "user", "content": build_user_message(capture, crop)}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {"claims": []}
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / "{0}.json".format(capture.get("id", "unknown"))).write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
        return validate_extraction_claims(payload.get("claims", []), active_ids)


class LocalBackend(ExtractionBackend):
    """Free local extraction via an Ollama server (no API cost).

    Uses the same enum-constrained JSON schema as the Claude backend, passed to
    Ollama's structured-output ``format`` field. Requires a running Ollama
    (``ollama serve``) with the model pulled (``ollama pull <model>``). Talks to it
    over HTTP with ``requests`` (already a project dependency); imported lazily.
    """

    name = "local"

    def __init__(self, model: str = DEFAULT_LOCAL_MODEL, host: Optional[str] = None,
                 cache_dir: Optional[Path] = None, timeout: int = 180) -> None:
        self.model = model
        self.host = (host or os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST).rstrip("/")
        self.cache_dir = cache_dir
        self.timeout = timeout

    def _system_text(self, params: List[Dict[str, Any]]) -> str:
        return (
            SYSTEM_INSTRUCTIONS
            + "\n\nManifest parameters (id | label | family | value_type | aliases):\n"
            + build_manifest_table(params)
            + "\n\nReturn JSON of the form {\"claims\": [ ... ]} matching the schema."
        )

    def extract(self, capture, crop, params):
        import requests  # project dependency; lazy so fixture-only envs need nothing

        active_ids = [p["parameter_id"] for p in params]
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_text(params)},
                {"role": "user", "content": build_user_message(capture, crop)},
            ],
            "stream": False,
            "format": build_output_schema(active_ids),
            "options": {"temperature": 0},
        }
        url = self.host + "/api/chat"
        try:
            resp = requests.post(url, json=body, timeout=self.timeout)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(
                "Could not reach Ollama at {0}. Start it with 'ollama serve' and "
                "pull the model with 'ollama pull {1}'.".format(self.host, self.model)
            ) from exc
        content = resp.json().get("message", {}).get("content", "")
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            payload = {"claims": []}
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / "{0}.json".format(capture.get("id", "unknown"))).write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
        return validate_extraction_claims(payload.get("claims", []), active_ids)


def make_backend(name: str, cache_dir: Optional[Path] = None,
                 model: str = DEFAULT_MODEL, host: Optional[str] = None) -> ExtractionBackend:
    if name == "fixture":
        return FixtureBackend(cache_dir=cache_dir)
    if name == "local":
        local_model = DEFAULT_LOCAL_MODEL if model == DEFAULT_MODEL else model
        return LocalBackend(model=local_model, host=host, cache_dir=cache_dir)
    if name == "llm":
        return ClaudeBackend(model=model, cache_dir=cache_dir)
    raise ValueError("unknown backend: {0} (use 'fixture', 'local', or 'llm')".format(name))


# --------------------------------------------------------------------------- #
# Run-level driver
# --------------------------------------------------------------------------- #
def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def extract_run(
    repo_root: Path,
    run_id: str,
    backend: ExtractionBackend,
    manifest_path: str = "config/parameters/core-crop-parameters.json",
) -> Dict[str, Any]:
    """Run extraction over a run's raw captures; write per-capture artifacts."""
    raw_dir = repo_root / "exploration" / "raw" / run_id
    out_dir = repo_root / "exploration" / "llm_extractions" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = _load_json(repo_root / manifest_path)
    manifest_version = manifest.get("manifest_version", "")
    params = active_parameters(manifest)

    raw_summary_path = raw_dir / "summary.json"
    crop = "crop"
    if raw_summary_path.exists():
        crop = _load_json(raw_summary_path).get("crop", crop)

    capture_files = sorted(p for p in raw_dir.glob("*.json") if p.name != "summary.json")
    total_claims = 0
    captures_with_claims = 0
    per_parameter: Dict[str, int] = {}
    for capture_file in capture_files:
        capture = _load_json(capture_file)
        claims = backend.extract(capture, crop, params)
        if claims:
            captures_with_claims += 1
        total_claims += len(claims)
        for c in claims:
            per_parameter[c["parameter_id"]] = per_parameter.get(c["parameter_id"], 0) + 1
        out = {
            "capture_id": capture.get("id"),
            "run_id": run_id,
            "crop": crop,
            "manifest_version": manifest_version,
            "backend": backend.name,
            "claims": claims,
        }
        (out_dir / "{0}.json".format(capture.get("id"))).write_text(
            json.dumps(out, indent=2) + "\n", encoding="utf-8"
        )

    summary = {
        "run_id": run_id,
        "backend": backend.name,
        "manifest_version": manifest_version,
        "captures": len(capture_files),
        "captures_with_claims": captures_with_claims,
        "extracted_claims": total_claims,
        "parameters_with_claims": len(per_parameter),
        "per_parameter": dict(sorted(per_parameter.items(), key=lambda kv: -kv[1])),
        "output_dir": str(out_dir.relative_to(repo_root)),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
