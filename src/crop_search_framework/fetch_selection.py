"""Phase B2 / WS-2 — fetch selection: turn the over-collected discovery ledger
into a balanced, auditable fetch queue.

Selection is the *only* place fetch decisions happen (the ledger records none).
We dedup by ``canonical_key`` while preserving the many-to-many association
(``ledger_ids`` + ``parameter_ids``), pre-filter junk/non-article/relevance-gated
candidates, then greedily select a queue balanced by source-tier trust, per-
parameter coverage, and **tier-aware domain caps** (curated institutional
domains get a high cap so they are never crowded out). For selected scholarly
candidates we optionally resolve a legal OA full-text URL (WS-4).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .backfill import is_junk_doi

ACCESS_RANK = {"open_full_text": 2, "metadata_only": 1, "unknown": 0}


def capture_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_ledger(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError("Discovery ledger not found: {0}".format(path))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_policy(repo_root: Path, policy_path: str) -> Dict[str, Any]:
    path = Path(policy_path)
    if not path.is_absolute():
        path = repo_root / path
    return json.loads(path.read_text(encoding="utf-8"))


def _is_trusted(domain: str, trusted: List[str]) -> bool:
    domain = (domain or "").lower()
    return any(domain == t or domain.endswith("." + t) or domain.endswith(t) for t in trusted)


def aggregate_candidates(ledger: List[Dict[str, Any]], policy: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collapse ledger rows to one candidate per canonical_key, keeping the
    full query/parameter association."""
    tier_trust = policy.get("tier_trust", {})
    groups: Dict[str, Dict[str, Any]] = {}
    for row in ledger:
        key = row["canonical_key"]
        cand = groups.get(key)
        if cand is None:
            cand = {
                "canonical_key": key,
                "source_url": row.get("source_url", ""),
                "doi": row.get("doi", ""),
                "result_type": row.get("result_type", ""),
                "source_domain": row.get("source_domain", ""),
                "source_tier": row.get("source_tier", ""),
                "score": row.get("score", 0),
                "access_status": row.get("access_status", "unknown"),
                "ledger_ids": [],
                "parameter_ids": set(),
                "_all_gated": True,
            }
            groups[key] = cand
        cand["ledger_ids"].append(row["ledger_id"])
        if row.get("parameter_id"):
            cand["parameter_ids"].add(row["parameter_id"])
        # Keep the highest-trust tier and the best score/access/url seen.
        if tier_trust.get(row.get("source_tier", ""), 0) > tier_trust.get(cand["source_tier"], 0):
            cand["source_tier"] = row.get("source_tier", "")
        if row.get("score", 0) > cand["score"]:
            cand["score"] = row.get("score", 0)
        if ACCESS_RANK.get(row.get("access_status", "unknown"), 0) > ACCESS_RANK.get(cand["access_status"], 0):
            cand["access_status"] = row.get("access_status", "unknown")
            cand["source_url"] = row.get("source_url", cand["source_url"])
        if row.get("discovery_drop_reason") != "relevance_gate":
            cand["_all_gated"] = False
    candidates = []
    for cand in groups.values():
        cand["parameter_ids"] = sorted(cand["parameter_ids"])
        candidates.append(cand)
    return candidates


def prefilter_reason(cand: Dict[str, Any], policy: Dict[str, Any]) -> str:
    """Return a skip reason for candidates that should never be fetched, else ''."""
    if policy.get("drop_relevance_gated", True) and cand.get("_all_gated"):
        return "relevance_gated"
    if cand.get("doi") and is_junk_doi(cand["doi"]):
        return "junk_doi"
    # Article-like allowlist (WS-4): keep reviews/meta-analyses, drop datasets etc.
    result_type = cand.get("result_type", "")
    if cand.get("doi") and result_type:
        if result_type not in set(policy.get("article_like_types", [])):
            return "non_article_type"
    return ""


def select_fetch_queue(
    repo_root: Path,
    run_id: str,
    policy_path: str = "config/fetch-policy/default.json",
    resolve_oa: bool = False,
    email: str = "",
) -> Dict[str, Any]:
    out_dir = repo_root / "exploration" / "discovery" / run_id
    ledger = load_ledger(out_dir / "results.jsonl")
    policy = load_policy(repo_root, policy_path)
    tier_trust = policy.get("tier_trust", {})
    trusted = policy.get("trusted_domains", [])
    target = int(policy.get("per_parameter_target", 4))

    candidates = aggregate_candidates(ledger, policy)
    queue: List[Dict[str, Any]] = []

    # Pre-filter unconditionally-skipped candidates.
    selectable: List[Dict[str, Any]] = []
    for cand in candidates:
        reason = prefilter_reason(cand, policy)
        if reason:
            queue.append(_queue_row(cand, selected=False, skip_reason=reason))
        else:
            selectable.append(cand)

    # Rank: tier trust, then open access, then score.
    selectable.sort(
        key=lambda c: (
            tier_trust.get(c["source_tier"], 0),
            ACCESS_RANK.get(c["access_status"], 0),
            c["score"],
        ),
        reverse=True,
    )

    domain_count: Dict[str, int] = defaultdict(int)
    param_coverage: Dict[str, int] = defaultdict(int)
    for cand in selectable:
        domain = cand["source_domain"]
        cap = policy.get("trusted_domain_cap", 50) if _is_trusted(domain, trusted) else policy.get("low_tier_domain_cap", 3)
        if domain_count[domain] >= cap:
            queue.append(_queue_row(cand, selected=False, skip_reason="domain_cap"))
            continue
        params = cand["parameter_ids"] or ["__unparameterized__"]
        if params and all(param_coverage[p] >= target for p in params):
            queue.append(_queue_row(cand, selected=False, skip_reason="param_saturated"))
            continue
        # Select.
        domain_count[domain] += 1
        for p in params:
            param_coverage[p] += 1
        row = _queue_row(cand, selected=True, skip_reason="")
        if resolve_oa and cand["access_status"] != "open_full_text" and cand.get("doi"):
            _attach_oa(row, cand["doi"], email)
        queue.append(row)

    _write_jsonl(out_dir / "fetch_queue.jsonl", queue)
    summary = _summary(run_id, ledger, candidates, queue, out_dir)
    (out_dir / "fetch_queue_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def _attach_oa(row: Dict[str, Any], doi: str, email: str) -> None:
    from .backfill import resolve_oa
    from .dev_tools.common import user_agent

    oa = resolve_oa(doi, email or "crop-search@example.org", user_agent())
    if oa:
        row["resolved_oa_url"] = oa["url"]
        row["oa_resolution_method"] = oa["resolver"]
        row["access_status"] = "open_full_text"


def _queue_row(cand: Dict[str, Any], selected: bool, skip_reason: str) -> Dict[str, Any]:
    return {
        "canonical_key": cand["canonical_key"],
        "source_url": cand["source_url"],
        "doi": cand.get("doi", ""),
        "source_domain": cand["source_domain"],
        "source_tier": cand["source_tier"],
        "score": cand["score"],
        "access_status": cand["access_status"],
        "parameter_ids": cand["parameter_ids"],
        "ledger_ids": cand["ledger_ids"],
        "fetch_selected": selected,
        "fetch_skip_reason": skip_reason,
    }


def _summary(run_id, ledger, candidates, queue, out_dir) -> Dict[str, Any]:
    from collections import Counter

    selected = [r for r in queue if r["fetch_selected"]]
    skip_counts = Counter(r["fetch_skip_reason"] for r in queue if not r["fetch_selected"])
    return {
        "run_id": run_id,
        "stage": "fetch_selection",
        "generated_at": capture_now(),
        "ledger_rows": len(ledger),
        "unique_candidates": len(candidates),
        "selected": len(selected),
        "skipped": len(queue) - len(selected),
        "skip_reason_counts": dict(sorted(skip_counts.items())),
        "selected_tier_counts": dict(sorted(Counter(r["source_tier"] for r in selected).items())),
        "selected_access_counts": dict(sorted(Counter(r["access_status"] for r in selected).items())),
        "fetch_queue_path": str((out_dir / "fetch_queue.jsonl").relative_to(out_dir.parents[2])),
    }


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
