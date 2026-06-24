"""Phase A — evaluation harness: retrieval gold set + extraction gold set.

Two independent scorers that separate *retrieval* quality (did discovery find and
select the right sources?) from *extraction* quality (did Opus read them right?).

- ``eval_extraction`` scores cached Opus extractions in
  ``exploration/llm_cache/<run>/<document_id>.json`` against hand-labeled gold
  records, computing precision/recall, parameter-mapping accuracy,
  unit-normalization correctness, and evidence faithfulness.
- ``eval_retrieval`` scores a discovery ledger
  (``exploration/discovery/<run>/results.jsonl``) and, when present, the fetch
  queue against expected authoritative sources per parameter/domain.

Both write ``exploration/eval/<run>/scorecard.{json,md}``. Both run offline.
Gold sets live under ``tests/golden/{extraction,retrieval}/<domain>.json``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

NUMERIC_TOLERANCE = 0.05  # 5% relative tolerance for numeric value matches.


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def capture_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_gold_records(gold_dir: Path, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load and flatten gold records from every ``<domain>.json`` in a dir.

    Gold files/records may carry a ``run_id``; when ``run_id`` is given, only
    records for that run (or with no run_id) are returned, so cross-run gold
    sets in one directory don't pollute a single run's score.
    """
    records: List[Dict[str, Any]] = []
    if not gold_dir.exists():
        return records
    for path in sorted(gold_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        domain = payload.get("domain", path.stem)
        file_run = payload.get("run_id")
        for record in payload.get("records", []):
            record = dict(record)
            record.setdefault("domain", domain)
            rec_run = record.get("run_id", file_run)
            if run_id is not None and rec_run is not None and rec_run != run_id:
                continue
            records.append(record)
    return records


def normalize_unit(unit: Optional[str]) -> str:
    if not unit:
        return ""
    return re.sub(r"\s+", "", str(unit).strip().lower())


def normalize_domain(value: str) -> str:
    value = (value or "").strip().lower()
    if "://" in value:
        value = urlparse(value).netloc
    return value[4:] if value.startswith("www.") else value


def normalize_doi(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.replace("https://doi.org/", "").replace("http://doi.org/", "")
    return value.strip("/")


def _pct(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


# --------------------------------------------------------------------------- #
# Value matching (extraction eval)
# --------------------------------------------------------------------------- #
def values_match(predicted: Dict[str, Any], gold: Dict[str, Any]) -> bool:
    """A prediction matches a gold record if their values are compatible."""
    gold_type = gold.get("value_type", "numeric")
    if gold_type == "numeric":
        return _numeric_match(predicted, gold)
    if gold_type == "range":
        return _range_match(predicted, gold)
    return _text_match(predicted, gold)


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _numeric_match(predicted: Dict[str, Any], gold: Dict[str, Any]) -> bool:
    target = _as_float(gold.get("numeric_value"))
    if target is None:
        return False
    candidates = [predicted.get("numeric_value")]
    # A predicted range whose span covers the gold point also counts.
    lo, hi = _as_float(predicted.get("range_min")), _as_float(predicted.get("range_max"))
    if lo is not None and hi is not None and lo <= target <= hi:
        return True
    for candidate in candidates:
        value = _as_float(candidate)
        if value is None:
            continue
        tol = max(abs(target) * NUMERIC_TOLERANCE, 1e-9)
        if abs(value - target) <= tol:
            return True
    return False


def _range_match(predicted: Dict[str, Any], gold: Dict[str, Any]) -> bool:
    g_lo, g_hi = _as_float(gold.get("range_min")), _as_float(gold.get("range_max"))
    if g_lo is None or g_hi is None:
        return False
    p_lo, p_hi = _as_float(predicted.get("range_min")), _as_float(predicted.get("range_max"))
    if p_lo is None or p_hi is None:
        point = _as_float(predicted.get("numeric_value"))
        return point is not None and g_lo <= point <= g_hi
    # Overlap of the two ranges.
    return p_lo <= g_hi and g_lo <= p_hi


def _text_match(predicted: Dict[str, Any], gold: Dict[str, Any]) -> bool:
    needle = str(gold.get("text_value", "")).strip().lower()
    if not needle:
        return True
    haystack = " ".join(
        str(predicted.get(key, "")) for key in ("claim_summary", "evidence_text")
    ).lower()
    return needle in haystack


def unit_matches(predicted: Dict[str, Any], gold: Dict[str, Any]) -> bool:
    gold_unit = normalize_unit(gold.get("unit"))
    if not gold_unit:
        return True
    return normalize_unit(predicted.get("unit")) == gold_unit


def evidence_is_faithful(predicted: Dict[str, Any], gold: Dict[str, Any]) -> bool:
    """The cited evidence text must actually contain the value/substring."""
    evidence = str(predicted.get("evidence_text", "")).lower()
    if not evidence:
        return False
    marker = gold.get("evidence_contains")
    if marker:
        return str(marker).lower() in evidence
    # Fall back to checking the numeric value appears in the evidence.
    target = _as_float(gold.get("numeric_value"))
    if target is None:
        return True
    numbers = {float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", evidence)}
    tol = max(abs(target) * NUMERIC_TOLERANCE, 1e-9)
    return any(abs(n - target) <= tol for n in numbers)


# --------------------------------------------------------------------------- #
# Extraction eval
# --------------------------------------------------------------------------- #
def load_extractions(llm_cache_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Return {document_id: [claim, ...]} from a run's llm_cache."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    if not llm_cache_dir.exists():
        return out
    for path in sorted(llm_cache_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        out[path.stem] = payload.get("claims", [])
    return out


def eval_extraction(repo_root: Path, run_id: str, gold_dir: Optional[Path] = None) -> Dict[str, Any]:
    gold_dir = gold_dir or repo_root / "tests" / "golden" / "extraction"
    gold = load_gold_records(gold_dir, run_id=run_id)
    extractions = load_extractions(repo_root / "exploration" / "llm_cache" / run_id)

    # Group gold by the (document_id, parameter_id) cell it adjudicates.
    gold_cells: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for record in gold:
        gold_cells.setdefault((record["document_id"], record["parameter_id"]), []).append(record)

    recalled_gold = 0
    param_recalled_gold = 0
    unit_correct = 0
    evidence_faithful = 0
    per_domain: Dict[str, Dict[str, int]] = {}

    for record in gold:
        domain = record["domain"]
        bucket = per_domain.setdefault(domain, {"gold": 0, "recalled": 0, "param_recalled": 0})
        bucket["gold"] += 1
        doc_claims = extractions.get(record["document_id"], [])
        same_param = [c for c in doc_claims if c.get("parameter_id") == record["parameter_id"]]
        if same_param:
            param_recalled_gold += 1
            bucket["param_recalled"] += 1
        value_hits = [c for c in same_param if values_match(c, record)]
        if value_hits:
            recalled_gold += 1
            bucket["recalled"] += 1
            best = value_hits[0]
            if unit_matches(best, record):
                unit_correct += 1
            if evidence_is_faithful(best, record):
                evidence_faithful += 1

    # Adjudicable precision: among predictions for a labeled (doc, param) cell,
    # how many value-match a gold record. Sparse gold can't penalize unlabeled
    # facts, so we only score cells we actually have ground truth for.
    adjudicable = 0
    adjudicable_correct = 0
    for (doc_id, param_id), records in gold_cells.items():
        for claim in extractions.get(doc_id, []):
            if claim.get("parameter_id") != param_id:
                continue
            adjudicable += 1
            if any(values_match(claim, record) for record in records):
                adjudicable_correct += 1

    report = {
        "run_id": run_id,
        "eval_type": "extraction",
        "generated_at": capture_now(),
        "gold_dir": relative_path_if_possible(gold_dir, repo_root),
        "gold_records": len(gold),
        "adjudicable_predictions": adjudicable,
        "metrics": {
            "recall": _pct(recalled_gold, len(gold)),
            "precision": _pct(adjudicable_correct, adjudicable),
            "parameter_mapping_accuracy": _pct(param_recalled_gold, len(gold)),
            "unit_normalization_correctness": _pct(unit_correct, recalled_gold),
            "evidence_faithfulness": _pct(evidence_faithful, recalled_gold),
        },
        "per_domain": {
            domain: {
                "gold": stats["gold"],
                "recall": _pct(stats["recalled"], stats["gold"]),
                "parameter_mapping_accuracy": _pct(stats["param_recalled"], stats["gold"]),
            }
            for domain, stats in sorted(per_domain.items())
        },
    }
    _write_scorecard(repo_root, run_id, "extraction", report)
    return report


# --------------------------------------------------------------------------- #
# Retrieval eval
# --------------------------------------------------------------------------- #
def load_ledger(ledger_path: Path) -> List[Dict[str, Any]]:
    if not ledger_path.exists():
        return []
    rows = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _ledger_matches_gold(row: Dict[str, Any], record: Dict[str, Any]) -> bool:
    gold_doi = normalize_doi(record.get("doi", ""))
    if gold_doi and normalize_doi(row.get("doi", "")) == gold_doi:
        return True
    gold_url = (record.get("expected_url") or "").strip().lower()
    if gold_url and (row.get("source_url") or "").strip().lower() == gold_url:
        return True
    gold_domain = normalize_domain(record.get("expected_domain", ""))
    if gold_domain and normalize_domain(row.get("source_url", "")) == gold_domain:
        return True
    return False


def eval_retrieval(repo_root: Path, run_id: str, gold_dir: Optional[Path] = None) -> Dict[str, Any]:
    gold_dir = gold_dir or repo_root / "tests" / "golden" / "retrieval"
    gold = load_gold_records(gold_dir, run_id=run_id)
    ledger_path = repo_root / "exploration" / "discovery" / run_id / "results.jsonl"
    queue_path = repo_root / "exploration" / "discovery" / run_id / "fetch_queue.jsonl"
    ledger = load_ledger(ledger_path)
    queue = load_ledger(queue_path)

    ledger_available = bool(ledger)
    selected_keys = {
        (row.get("canonical_key") or row.get("source_url") or "").strip().lower()
        for row in queue
        if row.get("fetch_selected")
    }

    in_ledger = 0
    selected = 0
    ranks: List[int] = []
    per_domain: Dict[str, Dict[str, int]] = {}

    for record in gold:
        domain = record["domain"]
        bucket = per_domain.setdefault(domain, {"gold": 0, "in_ledger": 0, "selected": 0})
        bucket["gold"] += 1
        matching_rows = [row for row in ledger if _ledger_matches_gold(row, record)]
        if matching_rows:
            in_ledger += 1
            bucket["in_ledger"] += 1
            best_rank = min(
                (int(row.get("discovery_rank", 0)) for row in matching_rows),
                default=0,
            )
            ranks.append(best_rank)
            keys = {
                (row.get("canonical_key") or row.get("source_url") or "").strip().lower()
                for row in matching_rows
            }
            if keys & selected_keys:
                selected += 1
                bucket["selected"] += 1

    report = {
        "run_id": run_id,
        "eval_type": "retrieval",
        "generated_at": capture_now(),
        "ledger_available": ledger_available,
        "ledger_rows": len(ledger),
        "gold_records": len(gold),
        "metrics": {
            "source_recall": _pct(in_ledger, len(gold)),
            "fetch_selection_recall": _pct(selected, len(gold)),
            "mean_rank": round(sum(ranks) / len(ranks), 2) if ranks else None,
        },
        "per_domain": {
            domain: {
                "gold": stats["gold"],
                "source_recall": _pct(stats["in_ledger"], stats["gold"]),
                "fetch_selection_recall": _pct(stats["selected"], stats["gold"]),
            }
            for domain, stats in sorted(per_domain.items())
        },
    }
    if not ledger_available:
        report["note"] = (
            "No discovery ledger found for this run. Retrieval scoring is pending until "
            "Phase B1 produces exploration/discovery/<run>/results.jsonl."
        )
    _write_scorecard(repo_root, run_id, "retrieval", report)
    return report


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def relative_path_if_possible(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _write_scorecard(repo_root: Path, run_id: str, eval_type: str, report: Dict[str, Any]) -> None:
    out_dir = repo_root / "exploration" / "eval" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scorecard-{0}.json".format(eval_type)).write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "scorecard-{0}.md".format(eval_type)).write_text(
        _render_markdown(report), encoding="utf-8"
    )


def _render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# {0} eval — {1}".format(report["eval_type"].capitalize(), report["run_id"]),
        "",
        "_Generated {0}_".format(report["generated_at"]),
        "",
        "## Overall metrics",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    for key, value in report["metrics"].items():
        lines.append("| {0} | {1} |".format(key, value))
    if report.get("note"):
        lines += ["", "> {0}".format(report["note"])]
    lines += ["", "## Per domain", "", "| Domain | " + " | ".join(
        k for k in next(iter(report["per_domain"].values())).keys()
    ) + " |" if report["per_domain"] else "_No gold records._"]
    if report["per_domain"]:
        first = next(iter(report["per_domain"].values()))
        lines.append("|---|" + "---|" * len(first))
        for domain, stats in report["per_domain"].items():
            lines.append("| {0} | {1} |".format(domain, " | ".join(str(v) for v in stats.values())))
    return "\n".join(lines) + "\n"
