"""Phase 2 — relationship lane back-half: select → fetch → corpus → matrix → eval.

Parallels the single-crop pipeline but is parameter-free and pair-centric. It
consumes the relationship discovery ledger (already produced by
``discover-relationships``) and carries the pair metadata all the way to a
populated crop×crop matrix.

Stages:
- ``select_relationship_fetch`` — ledger → fetch_queue (dedup by
  ``relationship_source_key``, balance per pair×mode, tier-aware domain caps).
- ``fetch_relationships`` — fetch+parse selected sources → relationship raw
  captures carrying the pair fields.
- ``build_relationship_corpus`` — reuse ``corpus.build_corpus`` + derive
  ``relationship_hits.jsonl`` (document_id → pair/mode).
- ``populate_relationship_matrix`` — matrix skeleton + claims + ledger →
  per-cell ``mode_statuses``.
- ``validate_relationship_claims`` — load/validate an extracted claims dir.
- ``eval_relationships`` — score the matrix against a relationship gold set.

Relationship CLAIMS are emitted by a separate extractor (subagent Opus pass)
into ``exploration/relationships/claims/<run>/<document_id>.json`` and validate
against ``crop-relationship-claim.schema.json`` — never the parameter contract.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .backfill import is_junk_doi
from . import corpus
from .relationships import (
    build_relationship_matrix,
    current_time,
    load_relationship_vocabulary,
    mode_directionality_map,
)

ACCESS_RANK = {"open_full_text": 2, "metadata_only": 1, "unknown": 0}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for r in rows:
            handle.write(json.dumps(r) + "\n")


def _load_policy(repo_root: Path, policy_path: str) -> Dict[str, Any]:
    p = Path(policy_path)
    if not p.is_absolute():
        p = repo_root / p
    return json.loads(p.read_text(encoding="utf-8"))


def _is_trusted(domain: str, trusted: List[str]) -> bool:
    domain = (domain or "").lower()
    return any(domain == t or domain.endswith("." + t) or domain.endswith(t) for t in trusted)


# --------------------------------------------------------------------------- #
# A1 — select-relationship-fetch
# --------------------------------------------------------------------------- #
def select_relationship_fetch(
    repo_root: Path,
    run_id: str,
    policy_path: str = "config/fetch-policy/default.json",
) -> Dict[str, Any]:
    out_dir = repo_root / "exploration" / "relationships" / "discovery" / run_id
    ledger = _read_jsonl(out_dir / "results.jsonl")
    if not ledger:
        raise FileNotFoundError("no relationship discovery ledger at {0}".format(out_dir / "results.jsonl"))
    policy = _load_policy(repo_root, policy_path)
    tier_trust = policy.get("tier_trust", {})
    trusted = policy.get("trusted_domains", [])
    target = int(policy.get("per_parameter_target", 4))  # reused as per-(pair×mode) target

    # Aggregate by relationship_source_key — the dedup unit (one source × one pair).
    groups: Dict[str, Dict[str, Any]] = {}
    for row in ledger:
        key = row["relationship_source_key"]
        cand = groups.get(key)
        if cand is None:
            cand = {
                "relationship_source_key": key,
                "canonical_relationship_key": row["canonical_relationship_key"],
                "ordered_pair_key": row["ordered_pair_key"],
                "subject_crop_id": row.get("subject_crop_id", ""),
                "object_crop_id": row.get("object_crop_id", ""),
                "node_mode": row.get("node_mode", "crop"),
                "subject_node_type": row.get("subject_node_type", ""),
                "subject_node_id": row.get("subject_node_id", ""),
                "object_node_type": row.get("object_node_type", ""),
                "object_node_id": row.get("object_node_id", ""),
                "subject_search_label": row.get("subject_search_label", ""),
                "object_search_label": row.get("object_search_label", ""),
                "relationship_mode": row["relationship_mode"],
                "relationship_subtype": row["relationship_subtype"],
                "source_url": row.get("source_url", ""),
                "doi": row.get("doi", ""),
                "result_type": row.get("result_type", ""),
                "source_domain": row.get("source_domain", ""),
                "source_tier": row.get("source_tier", ""),
                "score": row.get("score", 0),
                "access_status": row.get("access_status", "unknown"),
                "_all_gated": True,
            }
            groups[key] = cand
        if row.get("score", 0) > cand["score"]:
            cand["score"] = row.get("score", 0)
        if ACCESS_RANK.get(row.get("access_status", "unknown"), 0) > ACCESS_RANK.get(cand["access_status"], 0):
            cand["access_status"] = row.get("access_status", "unknown")
            cand["source_url"] = row.get("source_url", cand["source_url"])
        if row.get("discovery_drop_reason") != "relevance_gate":
            cand["_all_gated"] = False

    queue: List[Dict[str, Any]] = []
    selectable: List[Dict[str, Any]] = []
    for cand in groups.values():
        reason = _prefilter(cand, policy)
        if reason:
            queue.append(_queue_row(cand, False, reason))
        else:
            selectable.append(cand)

    selectable.sort(
        key=lambda c: (tier_trust.get(c["source_tier"], 0), ACCESS_RANK.get(c["access_status"], 0), c["score"]),
        reverse=True,
    )
    domain_count: Dict[str, int] = defaultdict(int)
    pair_mode_cov: Dict[str, int] = defaultdict(int)
    for cand in selectable:
        domain = cand["source_domain"]
        cap = policy.get("trusted_domain_cap", 50) if _is_trusted(domain, trusted) else policy.get("low_tier_domain_cap", 3)
        if domain_count[domain] >= cap:
            queue.append(_queue_row(cand, False, "domain_cap"))
            continue
        ckey = cand["canonical_relationship_key"]
        if pair_mode_cov[ckey] >= target:
            queue.append(_queue_row(cand, False, "pair_mode_saturated"))
            continue
        domain_count[domain] += 1
        pair_mode_cov[ckey] += 1
        queue.append(_queue_row(cand, True, ""))

    _write_jsonl(out_dir / "fetch_queue.jsonl", queue)
    selected = [r for r in queue if r["fetch_selected"]]
    summary = {
        "run_id": run_id,
        "stage": "relationship_fetch_selection",
        "generated_at": _now_iso(),
        "ledger_rows": len(ledger),
        "unique_source_pairs": len(groups),
        "selected": len(selected),
        "skipped": len(queue) - len(selected),
        "skip_reason_counts": dict(sorted(Counter(r["fetch_skip_reason"] for r in queue if not r["fetch_selected"]).items())),
        "distinct_pairs_selected": len({r["canonical_relationship_key"] for r in selected}),
    }
    (out_dir / "fetch_queue_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def _prefilter(cand: Dict[str, Any], policy: Dict[str, Any]) -> str:
    if policy.get("drop_relevance_gated", True) and cand.get("_all_gated"):
        return "relevance_gated"
    if cand.get("doi") and is_junk_doi(cand["doi"]):
        return "junk_doi"
    rt = cand.get("result_type", "")
    if cand.get("doi") and rt and rt not in set(policy.get("article_like_types", [])):
        return "non_article_type"
    return ""


def _queue_row(cand: Dict[str, Any], selected: bool, skip: str) -> Dict[str, Any]:
    return {
        "relationship_source_key": cand["relationship_source_key"],
        "canonical_relationship_key": cand["canonical_relationship_key"],
        "ordered_pair_key": cand["ordered_pair_key"],
        "subject_crop_id": cand.get("subject_crop_id", ""),
        "object_crop_id": cand.get("object_crop_id", ""),
        "node_mode": cand.get("node_mode", "crop"),
        "subject_node_type": cand.get("subject_node_type", ""),
        "subject_node_id": cand.get("subject_node_id", ""),
        "object_node_type": cand.get("object_node_type", ""),
        "object_node_id": cand.get("object_node_id", ""),
        "subject_search_label": cand.get("subject_search_label", ""),
        "object_search_label": cand.get("object_search_label", ""),
        "relationship_mode": cand["relationship_mode"],
        "relationship_subtype": cand["relationship_subtype"],
        "source_url": cand["source_url"],
        "doi": cand.get("doi", ""),
        "source_domain": cand["source_domain"],
        "source_tier": cand["source_tier"],
        "score": cand["score"],
        "access_status": cand["access_status"],
        "fetch_selected": selected,
        "fetch_skip_reason": skip,
    }


# --------------------------------------------------------------------------- #
# A2 — fetch-relationships
# --------------------------------------------------------------------------- #
def fetch_relationships(
    repo_root: Path,
    run_id: str,
    resume: bool = False,
    limit: Optional[int] = None,
    crop: str = "",
) -> Dict[str, Any]:
    from .dev_tools.common import user_agent
    from .dev_tools.fetch_web import infer_document_type, pick_suffix, safe_name, extract_title_hint
    from .dev_tools.http_client import HttpClient, HttpError
    from .dev_tools.parse_document import parse_html, parse_pdf
    from .schema_registry import SchemaRegistry

    registry = SchemaRegistry(repo_root)
    disc = repo_root / "exploration" / "relationships" / "discovery" / run_id
    queue = [r for r in _read_jsonl(disc / "fetch_queue.jsonl") if r.get("fetch_selected")]
    if limit:
        queue = queue[:limit]
    raw_dir = repo_root / "exploration" / "relationships" / "raw" / run_id
    fetched_dir = raw_dir / "fetched"
    fetched_dir.mkdir(parents=True, exist_ok=True)
    client = HttpClient(cache_dir=repo_root / "exploration" / "cache" / "fetch")
    ua = user_agent()

    # Fetch each unique URL once; emit one capture per selected (url × pair) row.
    parsed_by_url: Dict[str, Any] = {}
    captures = 0
    fetch_ok = fetch_fail = 0
    by_url_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in queue:
        by_url_rows[row["source_url"]].append(row)

    idx = 0
    for url, rows in by_url_rows.items():
        parsed, meta, ok = _fetch_one(client, url, len(parsed_by_url) + 1, ua, raw_dir, repo_root,
                                      infer_document_type, pick_suffix, safe_name, extract_title_hint,
                                      parse_html, parse_pdf)
        if ok:
            fetch_ok += 1
        else:
            fetch_fail += 1
        for row in rows:
            idx += 1
            cap = _relationship_capture(run_id, idx, row, parsed, meta, ok, crop)
            registry.validate("raw-capture.schema.json", cap)
            path = raw_dir / "{0}.json".format(cap["id"])
            if not (resume and path.exists()):
                path.write_text(json.dumps(cap, indent=2) + "\n", encoding="utf-8")
            captures += 1

    summary = {
        "run_id": run_id,
        "stage": "fetch_relationships",
        "generated_at": _now_iso(),
        "crop": crop,
        "selected_rows": len(queue),
        "unique_urls": len(by_url_rows),
        "captures_written": captures,
        "fetch_successes": fetch_ok,
        "fetch_failures": fetch_fail,
        "failure_count": fetch_fail,
    }
    (raw_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def _fetch_one(client, url, index, ua, raw_dir, repo_root, infer_document_type, pick_suffix,
               safe_name, extract_title_hint, parse_html, parse_pdf):
    from .dev_tools.http_client import HttpError
    try:
        resp = client.get_binary(url, headers={"User-Agent": ua}, timeout=35)
    except HttpError:
        return None, None, False
    final_url = resp.url or url
    ctype = resp.headers.get("content-type") or resp.headers.get("Content-Type") or ""
    dtype = infer_document_type(final_url, ctype)
    domain = urlparse(final_url).netloc.lower()
    artifact = raw_dir / "fetched" / "{0:03d}-{1}{2}".format(index, safe_name(domain or "source"), pick_suffix(final_url, dtype))
    artifact.write_bytes(resp.content)
    title = extract_title_hint(resp.text) if dtype == "html" else ""
    meta = {"final_url": final_url, "content_type": ctype, "document_type": dtype,
            "artifact_path": str(artifact.relative_to(repo_root)), "title_hint": title}
    try:
        parsed = parse_pdf(artifact) if dtype == "pdf" else parse_html(artifact)
    except Exception:
        return meta, meta, False
    if not parsed.get("raw_text"):
        return meta, meta, False
    return parsed, meta, True


def _relationship_capture(run_id, idx, row, parsed, meta, ok, crop):
    sanitized = run_id.lower().replace("_", "-")
    base = {
        "id": "{0}-relcap-{1:04d}".format(sanitized, idx),
        "run_id": run_id,
        "query": "crop_relationship",
        "query_kind": "crop_relationship",
        "subject_crop_id": row.get("subject_crop_id", ""),
        "object_crop_id": row.get("object_crop_id", ""),
        "node_mode": row.get("node_mode", "crop"),
        "subject_node_type": row.get("subject_node_type", ""),
        "subject_node_id": row.get("subject_node_id", ""),
        "object_node_type": row.get("object_node_type", ""),
        "object_node_id": row.get("object_node_id", ""),
        "subject_search_label": row.get("subject_search_label", ""),
        "object_search_label": row.get("object_search_label", ""),
        "relationship_mode": row["relationship_mode"],
        "relationship_subtype": row["relationship_subtype"],
        "ordered_pair_key": row["ordered_pair_key"],
        "canonical_relationship_key": row["canonical_relationship_key"],
        "source_tier_id": row.get("source_tier", ""),
        "source_metadata": {"doi": row.get("doi", "")},
        "source_url": row["source_url"],
        "source_domain": row.get("source_domain", urlparse(row["source_url"]).netloc.lower()),
        "accessed_at": _now_iso(),
        "search_title": "",
    }
    if not ok or parsed is None or parsed is meta:
        base.update({
            "access_status": "metadata_only", "final_url": row["source_url"],
            "source_title": row["source_url"], "content_type": "", "document_type": "other",
            "artifact_path": "", "snippet": "", "raw_text": "",
            "parser_used": "metadata-only", "candidate_claims": [], "failures": [], "status": "captured",
        })
        return base
    base.update({
        "access_status": "open_full_text", "final_url": meta["final_url"],
        "source_title": parsed.get("title_hint") or meta.get("title_hint") or row["source_url"],
        "content_type": meta["content_type"], "document_type": meta["document_type"],
        "artifact_path": meta["artifact_path"], "snippet": parsed.get("snippet", ""),
        "raw_text": parsed["raw_text"], "parser_used": parsed.get("parser_used", "parse-document"),
        "candidate_claims": parsed.get("candidate_claims", []), "failures": parsed.get("failures", []),
        "status": parsed.get("status", "parsed"),
    })
    return base


# --------------------------------------------------------------------------- #
# A2.5 — build relationship corpus (reuse build_corpus + derive relationship_hits)
# --------------------------------------------------------------------------- #
def build_relationship_corpus(repo_root: Path, run_id: str) -> Dict[str, Any]:
    raw_dir = repo_root / "exploration" / "relationships" / "raw" / run_id
    out_dir = repo_root / "exploration" / "relationships" / "corpus" / run_id
    result = corpus.build_corpus(repo_root, run_id, raw_dir=raw_dir, out_dir=out_dir)

    # Map source_url -> document_id from the built documents.
    url_to_doc: Dict[str, str] = {}
    for f in sorted((out_dir / "documents").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        url_to_doc[d.get("source_url", "")] = d["document_id"]

    # Derive relationship_hits from the raw captures' pair fields.
    hits = []
    for f in sorted(raw_dir.glob("*.json")):
        if f.name == "summary.json":
            continue
        cap = json.loads(f.read_text(encoding="utf-8"))
        doc_id = url_to_doc.get(cap.get("source_url", ""))
        if not doc_id or not cap.get("canonical_relationship_key"):
            continue
        hits.append({
            "document_id": doc_id,
            "canonical_relationship_key": cap["canonical_relationship_key"],
            "ordered_pair_key": cap.get("ordered_pair_key", ""),
            "subject_crop_id": cap.get("subject_crop_id", ""),
            "object_crop_id": cap.get("object_crop_id", ""),
            "node_mode": cap.get("node_mode", "crop"),
            "subject_node_type": cap.get("subject_node_type", ""),
            "subject_node_id": cap.get("subject_node_id", ""),
            "object_node_type": cap.get("object_node_type", ""),
            "object_node_id": cap.get("object_node_id", ""),
            "relationship_mode": cap.get("relationship_mode", ""),
            "relationship_subtype": cap.get("relationship_subtype", ""),
        })
    _write_jsonl(out_dir / "relationship_hits.jsonl", hits)
    result["relationship_hits"] = len(hits)
    return result


# --------------------------------------------------------------------------- #
# A3 (validation half) — load/validate extracted relationship claims
# --------------------------------------------------------------------------- #
# Paired subject/object fields swapped together when a symmetric claim's
# endpoints are reordered into canonical order.
_SYMMETRIC_PAIR_FIELDS = (
    ("subject_crop_id", "object_crop_id"),
    ("subject_crop_group", "object_crop_group"),
    ("subject_node_type", "object_node_type"),
    ("subject_node_id", "object_node_id"),
    ("subject_crop_label", "object_crop_label"),
)


def _claim_endpoint_token(claim: Dict[str, Any], side: str) -> str:
    """The identity token used to canonically order a symmetric claim's
    endpoints: node id when present, else crop id."""
    return claim.get("{0}_node_id".format(side)) or claim.get("{0}_crop_id".format(side)) or ""


def normalize_symmetric_claims(claims: List[Dict[str, Any]], directionality: Dict[str, str]) -> List[Dict[str, Any]]:
    """For symmetric-mode claims, reorder endpoints into canonical (sorted) order
    and recompute the keys, so matrix population and graph lookup are
    order-independent regardless of which order an extractor emitted. Directional
    claims pass through untouched. Mutates and returns the claims."""
    for claim in claims:
        if directionality.get(claim.get("relationship_mode", "")) != "symmetric":
            continue
        if _claim_endpoint_token(claim, "subject") > _claim_endpoint_token(claim, "object"):
            for subject_field, object_field in _SYMMETRIC_PAIR_FIELDS:
                if subject_field in claim and object_field in claim:
                    claim[subject_field], claim[object_field] = claim[object_field], claim[subject_field]
        left = _claim_endpoint_token(claim, "subject")
        right = _claim_endpoint_token(claim, "object")
        mode = claim.get("relationship_mode", "")
        claim["canonical_relationship_key"] = "{0}|{1}|{2}".format(mode, left, right)
        claim["ordered_pair_key"] = "{0}|{1}".format(left, right)
    return claims


def validate_relationship_claims(repo_root: Path, run_id: str) -> Dict[str, Any]:
    from .schema_registry import SchemaRegistry
    registry = SchemaRegistry(repo_root)
    claims_dir = repo_root / "exploration" / "relationships" / "claims" / run_id
    claims: List[Dict[str, Any]] = []
    invalid: List[str] = []
    for f in sorted(claims_dir.glob("*.json")):
        payload = json.loads(f.read_text(encoding="utf-8"))
        for claim in payload.get("claims", []):
            try:
                registry.validate("crop-relationship-claim.schema.json", claim)
                claims.append(claim)
            except Exception as exc:
                invalid.append("{0}: {1}".format(f.name, str(exc)[:120]))
    # Code-enforced canonicalization for symmetric modes (intercrop, strip_crop,
    # mixed_crop, companion_crop): never trust the extractor's emitted endpoint
    # order. Both matrix population and the graph read claims through here.
    directionality = mode_directionality_map(load_relationship_vocabulary(repo_root))
    normalize_symmetric_claims(claims, directionality)
    return {"run_id": run_id, "claims": claims, "valid_count": len(claims), "invalid": invalid}


# --------------------------------------------------------------------------- #
# A4 — populate-relationship-matrix
# --------------------------------------------------------------------------- #
def populate_relationship_matrix(
    repo_root: Path,
    run_id: str,
    mode_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    matrix = build_relationship_matrix(repo_root, mode_ids=mode_ids)
    disc = repo_root / "exploration" / "relationships" / "discovery" / run_id
    ledger = _read_jsonl(disc / "results.jsonl")
    searched_keys = {r["canonical_relationship_key"] for r in ledger}

    loaded = validate_relationship_claims(repo_root, run_id)
    accepted = [c for c in loaded["claims"] if c.get("status") in ("accepted", "needs_review")]
    by_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in accepted:
        by_key[c["canonical_relationship_key"]].append(c)

    populated = 0
    for cell in matrix["cells"]:
        for mode_id, st in cell["mode_statuses"].items():
            ckey = st["canonical_relationship_key"]
            claims = by_key.get(ckey, [])
            if claims:
                effects = [c["effect"] for c in claims if c.get("effect") not in (None, "unknown")]
                distinct = set(effects)
                st["evidence_count"] = len(claims)
                if len({e for e in distinct if e in ("beneficial", "compatible")}) and \
                   len({e for e in distinct if e in ("incompatible", "avoid")}):
                    st["status"] = "conflicting_evidence"
                    st["conflict_count"] = len(distinct)
                else:
                    st["status"] = "evidence_found"
                st["summary_effect"] = Counter(effects).most_common(1)[0][0] if effects else "unknown"
                populated += 1
            elif ckey in searched_keys:
                st["status"] = "searched_no_evidence"

    out_dir = repo_root / "exploration" / "relationships" / "matrix"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "populated-{0}.json".format(run_id)).write_text(json.dumps(matrix, indent=2) + "\n", encoding="utf-8")
    status_counts: Counter = Counter()
    for cell in matrix["cells"]:
        for st in cell["mode_statuses"].values():
            status_counts[st["status"]] += 1
    return {
        "run_id": run_id,
        "generated_at": current_time(),
        "cells": len(matrix["cells"]),
        "claims_used": len(accepted),
        "cells_with_evidence": populated,
        "status_counts": dict(sorted(status_counts.items())),
    }


# --------------------------------------------------------------------------- #
# A5 — eval-relationships
# --------------------------------------------------------------------------- #
def eval_relationships(repo_root: Path, run_id: str, gold_dir: Optional[Path] = None) -> Dict[str, Any]:
    gold_dir = gold_dir or repo_root / "tests" / "golden" / "relationships"
    gold: List[Dict[str, Any]] = []
    if gold_dir.exists():
        for f in sorted(gold_dir.glob("*.json")):
            payload = json.loads(f.read_text(encoding="utf-8"))
            for rec in payload.get("records", []):
                rec.setdefault("mode", payload.get("mode", ""))
                gold.append(rec)
    matrix_path = repo_root / "exploration" / "relationships" / "matrix" / "populated-{0}.json".format(run_id)
    cells = json.loads(matrix_path.read_text(encoding="utf-8"))["cells"] if matrix_path.exists() else []
    cell_by_pair_mode: Dict[tuple, Dict[str, Any]] = {}
    for cell in cells:
        for mode_id, st in cell["mode_statuses"].items():
            cell_by_pair_mode[(cell["ordered_pair_key"], mode_id)] = st

    found = effect_ok = 0
    for rec in gold:
        st = cell_by_pair_mode.get((rec["ordered_pair_key"], rec["mode"]))
        if st and st["status"] in ("evidence_found", "conflicting_evidence"):
            found += 1
            if st["summary_effect"] == rec.get("expected_effect"):
                effect_ok += 1
    report = {
        "run_id": run_id,
        "eval_type": "relationships",
        "generated_at": _now_iso(),
        "matrix_available": bool(cells),
        "gold_records": len(gold),
        "metrics": {
            "pair_recall": round(found / len(gold), 4) if gold else 0.0,
            "effect_accuracy": round(effect_ok / found, 4) if found else 0.0,
        },
    }
    out_dir = repo_root / "exploration" / "eval" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scorecard-relationships.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


# --------------------------------------------------------------------------- #
# B1 — node catalog
# --------------------------------------------------------------------------- #
NODE_CATALOG_PATH = "config/relationships/node-catalog.json"

# Aggregate node types the resolver can infer minor-crop relationships from,
# in priority order (most specific first).
_AGGREGATE_BASES = ("botanical_family", "functional_group", "genus")

# Evidence statuses that count as usable in the graph. `rejected` / `conflict`
# are schema-valid but must never surface as resolver evidence — this mirrors
# the accept policy in populate_relationship_matrix.
_EVIDENCE_STATUSES = ("accepted", "needs_review")


def load_node_catalog(repo_root: Path) -> Dict[str, Any]:
    """Load the hybrid relationship node catalog (no schema validation here;
    callers that need it validate via SchemaRegistry)."""
    return json.loads((repo_root / NODE_CATALOG_PATH).read_text(encoding="utf-8"))


def _node_alias_index(catalog: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Map case-insensitive alias / id / label -> node. Crop nodes win on
    collision so a crop name never resolves to a family/genus node."""
    index: Dict[str, Dict[str, Any]] = {}
    ordered = sorted(catalog.get("nodes", []), key=lambda n: 0 if n.get("node_type") == "crop" else 1)
    for node in ordered:
        keys = [node["node_id"], node.get("label", "")]
        keys.extend(node.get("aliases", []))
        for key in keys:
            norm = (key or "").strip().lower()
            if norm and norm not in index:
                index[norm] = node
    return index


def _aggregate_ids(node: Dict[str, Any], base: str) -> List[str]:
    if base == "botanical_family":
        value = node.get("botanical_family")
        return [value] if value else []
    if base == "genus":
        value = node.get("genus")
        return [value] if value else []
    if base == "functional_group":
        return list(node.get("functional_groups", []))
    return []


# --------------------------------------------------------------------------- #
# B2 — relationship evidence graph
# --------------------------------------------------------------------------- #
def _claim_node_tuple(claim: Dict[str, Any], side: str):
    """Normalize a claim side to a (node_type, node_id) tuple. Honors explicit
    node fields; falls back to synthesizing a crop node from the legacy
    crop fields so old-style crop-only claims still index."""
    node_type = claim.get("{0}_node_type".format(side))
    node_id = claim.get("{0}_node_id".format(side))
    if node_type and node_id:
        return (node_type, node_id)
    crop_id = claim.get("{0}_crop_id".format(side))
    if crop_id:
        return ("crop", crop_id)
    return None


def _summary_effect(claims: List[Dict[str, Any]]) -> str:
    effects = [c.get("effect") for c in claims if c.get("effect") not in (None, "unknown")]
    if not effects:
        return "unknown"
    return Counter(effects).most_common(1)[0][0]


def build_relationship_graph(repo_root: Path, run_id: str) -> Dict[str, Any]:
    """Build an evidence graph from validated, evidence-bearing relationship
    claims. Indexes are keyed by (relationship_mode, subject_tuple, object_tuple)
    so evidence for one mode never bleeds into another."""
    loaded = validate_relationship_claims(repo_root, run_id)
    claims = [c for c in loaded["claims"] if c.get("status") in _EVIDENCE_STATUSES]

    direct: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    aggregate: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    host_overlays: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for claim in claims:
        mode = claim.get("relationship_mode", "")
        subject = _claim_node_tuple(claim, "subject")
        obj = _claim_node_tuple(claim, "object")
        if subject is None or obj is None:
            continue
        s_type, s_id = subject
        o_type, o_id = obj
        if s_type == "host_group":
            # Host-risk overlay: keyed by mode + host group, applied to any crop
            # pair that shares that host group.
            host_overlays["{0}|{1}".format(mode, s_id)].append(claim)
        elif s_type == "crop" and o_type == "crop":
            direct["{0}|{1}|{2}".format(mode, s_id, o_id)].append(claim)
        elif s_type in _AGGREGATE_BASES and o_type in _AGGREGATE_BASES:
            aggregate["{0}|{1}|{2}".format(mode, s_id, o_id)].append(claim)

    graph = {
        "run_id": run_id,
        "generated_at": current_time(),
        "claim_count": len(claims),
        # Persisted so the resolver can sort symmetric-mode queries without
        # re-loading the vocabulary. Claims are already normalized to canonical
        # endpoint order (see normalize_symmetric_claims), so symmetric direct /
        # aggregate index keys are sorted.
        "mode_directionality": mode_directionality_map(load_relationship_vocabulary(repo_root)),
        "direct": dict(direct),
        "aggregate": dict(aggregate),
        "host_overlays": dict(host_overlays),
    }
    out_dir = repo_root / "exploration" / "relationships" / "graph"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "{0}.json".format(run_id)).write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
    return graph


def _load_relationship_graph(repo_root: Path, run_id: str) -> Dict[str, Any]:
    path = repo_root / "exploration" / "relationships" / "graph" / "{0}.json".format(run_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _host_caveats(claims: List[Dict[str, Any]], host_group: str) -> List[Dict[str, Any]]:
    caveats = []
    for claim in claims:
        context = claim.get("context") or {}
        caveats.append({
            "host_group": host_group,
            "risk_factor": context.get("risk_factor", ""),
            "effect": claim.get("effect", "unknown"),
            "evidence_text": claim.get("evidence_text", ""),
            "relationship_claim_id": claim.get("relationship_claim_id", ""),
        })
    return caveats


def resolve_crop_relationship(
    repo_root: Path,
    run_id: str,
    subject: str,
    object: str,
    mode: str = "rotation",
) -> Dict[str, Any]:
    """Resolve a (subject, object) crop relationship for one mode. Checks exact
    crop evidence first, then group inference, and always overlays host-risk
    caveats shared by both crops."""
    catalog = load_node_catalog(repo_root)
    graph = _load_relationship_graph(repo_root, run_id)
    alias_index = _node_alias_index(catalog)

    result: Dict[str, Any] = {
        "run_id": run_id,
        "mode": mode,
        "subject": subject,
        "object": object,
        "status": "no_evidence",
        "primary_effect": "unknown",
        "inference_basis": "",
        "status_flags": [],
        "caveats": [],
        "unknown_nodes": [],
    }

    subject_node = alias_index.get(subject.strip().lower())
    object_node = alias_index.get(object.strip().lower())
    unknown = [raw for raw, node in ((subject, subject_node), (object, object_node)) if node is None]
    if unknown:
        result["unknown_nodes"] = unknown
        return result

    s_id = subject_node["node_id"]
    o_id = object_node["node_id"]

    # Symmetric modes (intercrop, strip_crop, mixed_crop, companion_crop) index
    # evidence under sorted endpoints; sort the query the same way so (a,b) and
    # (b,a) hit the same entry.
    symmetric = graph.get("mode_directionality", {}).get(mode) == "symmetric"

    def _pair(a: str, b: str):
        return tuple(sorted([a, b])) if symmetric else (a, b)

    ds, do = _pair(s_id, o_id)
    direct_claims = graph.get("direct", {}).get("{0}|{1}|{2}".format(mode, ds, do), [])
    if direct_claims:
        result["status"] = "direct_evidence"
        result["primary_effect"] = _summary_effect(direct_claims)
    else:
        for base in _AGGREGATE_BASES:
            subject_aggs = _aggregate_ids(subject_node, base)
            object_aggs = _aggregate_ids(object_node, base)
            matched = None
            for sa in subject_aggs:
                for oa in object_aggs:
                    asa, aoa = _pair(sa, oa)
                    claims = graph.get("aggregate", {}).get("{0}|{1}|{2}".format(mode, asa, aoa), [])
                    if claims:
                        matched = claims
                        break
                if matched:
                    break
            if matched:
                result["status"] = "inferred_from_group"
                result["inference_basis"] = base
                result["primary_effect"] = _summary_effect(matched)
                break

    # Host-risk overlay: any host group shared by both crops with overlay claims.
    shared_hosts = set(subject_node.get("host_groups", [])) & set(object_node.get("host_groups", []))
    for host_group in sorted(shared_hosts):
        overlay_claims = graph.get("host_overlays", {}).get("{0}|{1}".format(mode, host_group), [])
        caveats = _host_caveats(overlay_claims, host_group)
        if caveats:
            result["caveats"].extend(caveats)
            if "host_risk_caveat" not in result["status_flags"]:
                result["status_flags"].append("host_risk_caveat")

    return result


# --------------------------------------------------------------------------- #
# B3 — cross-lane span dedup guard
# --------------------------------------------------------------------------- #
def _normalize_span(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def _iter_param_claims(param_dir: Path):
    for path in sorted(param_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "claims" in payload:
            for claim in payload["claims"]:
                yield claim
        else:
            yield payload


# Parameter ids whose spans could legitimately overlap a relationship claim and
# so must not be double-emitted. Intercropping has no dedicated parameter today,
# so this set is forward-looking; extraction routing (the prompt) is the live
# boundary. Add intercrop/companion parameter ids here if they are ever defined.
DEFAULT_RELATIONSHIP_PARAMETER_IDS = ("management.rotation_recommendation",)


def relationship_parameter_span_conflicts(
    repo_root: Path,
    run_id: str,
    param_run: str,
    parameter_id=None,
) -> Dict[str, Any]:
    """Detect evidence spans emitted as BOTH a relationship claim and a
    management parameter claim. The same span must live in exactly one lane.

    `parameter_id` accepts a single id or an iterable of ids; defaults to the
    relationship-adjacent parameter set."""
    if parameter_id is None:
        parameter_ids = set(DEFAULT_RELATIONSHIP_PARAMETER_IDS)
    elif isinstance(parameter_id, str):
        parameter_ids = {parameter_id}
    else:
        parameter_ids = set(parameter_id)

    loaded = validate_relationship_claims(repo_root, run_id)
    spans: Dict[str, str] = {}
    for claim in loaded["claims"]:
        norm = _normalize_span(claim.get("evidence_text", ""))
        if norm:
            spans.setdefault(norm, claim.get("relationship_claim_id", ""))

    param_dir = repo_root / "exploration" / "normalized" / param_run
    conflicts: List[Dict[str, Any]] = []
    if param_dir.exists():
        for claim in _iter_param_claims(param_dir):
            if claim.get("parameter_id") not in parameter_ids:
                continue
            evidence = ((claim.get("provenance") or {}).get("evidence_text", ""))
            norm = _normalize_span(evidence)
            if norm and norm in spans:
                conflicts.append({
                    "parameter_claim_id": claim.get("claim_id", ""),
                    "relationship_claim_id": spans[norm],
                    "parameter_id": claim.get("parameter_id", ""),
                    "evidence_text": evidence,
                })

    return {
        "run_id": run_id,
        "param_run": param_run,
        "parameter_ids": sorted(parameter_ids),
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
    }
