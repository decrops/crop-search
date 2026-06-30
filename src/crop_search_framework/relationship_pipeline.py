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
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote, urlparse

from .backfill import is_junk_doi
from . import corpus
from .relationships import (
    build_relationship_matrix,
    current_time,
    load_crop_universe,
    load_relationship_vocabulary,
    mode_directionality_map,
    unordered_crop_pairs,
)
from .source_tiers import tier_band, tier_rank, tier_rank_index

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


def crop_reference_url(crop) -> str:
    """The crop's main encyclopedia reference article. Wikipedia redirects
    synonyms (Corn -> Maize, Oilseed rape -> Rapeseed), so the label works as the
    lookup term and the fetcher follows the redirect to the canonical article."""
    term = (getattr(crop, "label", "") or crop.crop_id).strip().replace(" ", "_")
    return "https://en.wikipedia.org/wiki/{0}".format(quote(term))


def fetch_crop_references(
    repo_root: Path,
    run_id: str,
    crop_dir: str = "config/crops",
    limit: Optional[int] = None,
    crop_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Fetch each crop's main reference article (Wikipedia) into the relationship
    raw layer, bypassing pair-template discovery. These parse cleanly as HTML and
    carry rotation/relationship prose for the well-known crops that hostile
    publisher PDFs and noisy searches fail to deliver. The downstream
    build-relationship-corpus -> extraction flow is unchanged."""
    from .dev_tools.common import user_agent
    from .dev_tools.fetch_web import infer_document_type, pick_suffix, safe_name, extract_title_hint
    from .dev_tools.http_client import HttpClient
    from .dev_tools.parse_document import parse_html, parse_pdf
    from .schema_registry import SchemaRegistry
    from .relationships import load_crop_universe

    registry = SchemaRegistry(repo_root)
    crops = load_crop_universe(repo_root, crop_dir)
    if crop_ids:
        wanted = set(crop_ids)
        crops = [c for c in crops if c.crop_id in wanted]
    if limit:
        crops = crops[:limit]

    raw_dir = repo_root / "exploration" / "relationships" / "raw" / run_id
    (raw_dir / "fetched").mkdir(parents=True, exist_ok=True)
    client = HttpClient(cache_dir=repo_root / "exploration" / "cache" / "fetch")
    ua = user_agent()

    captures = ok = fail = 0
    resolved: List[Dict[str, str]] = []
    for idx, crop in enumerate(crops, 1):
        url = crop_reference_url(crop)
        parsed, meta, good = _fetch_one(client, url, idx, ua, raw_dir, repo_root,
                                        infer_document_type, pick_suffix, safe_name,
                                        extract_title_hint, parse_html, parse_pdf)
        ok += 1 if good else 0
        fail += 0 if good else 1
        final_url = (meta or {}).get("final_url", url)
        row = {
            "subject_crop_id": crop.crop_id, "object_crop_id": crop.crop_id,
            "subject_crop_label": crop.label, "object_crop_label": crop.label,
            "node_mode": "crop", "subject_node_type": "crop", "subject_node_id": crop.crop_id,
            "object_node_type": "crop", "object_node_id": crop.crop_id,
            "subject_search_label": crop.search_term, "object_search_label": crop.search_term,
            "relationship_mode": "reference", "relationship_subtype": "crop_reference",
            "ordered_pair_key": "{0}|{0}".format(crop.crop_id),
            "canonical_relationship_key": "reference|{0}|{0}".format(crop.crop_id),
            "source_tier": "reference_encyclopedia", "doi": "",
            "source_url": final_url,
            "source_domain": urlparse(final_url).netloc.lower(),
        }
        cap = _relationship_capture(run_id, idx, row, parsed, meta, good, crop.search_term)
        registry.validate("raw-capture.schema.json", cap)
        (raw_dir / "{0}.json".format(cap["id"])).write_text(json.dumps(cap, indent=2) + "\n", encoding="utf-8")
        captures += 1
        resolved.append({"crop_id": crop.crop_id, "reference_url": final_url,
                         "access_status": cap["access_status"]})

    summary = {
        "run_id": run_id,
        "stage": "fetch_crop_references",
        "generated_at": _now_iso(),
        "crops": len(crops),
        "captures_written": captures,
        "fetch_successes": ok,
        "fetch_failures": fail,
        "references": resolved,
    }
    (raw_dir / "reference_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


# Markers of bot-challenge / access-denied pages that publishers (MDPI, Cloudflare,
# reCAPTCHA, etc.) return with HTTP 200; these carry no extractable evidence.
_CHALLENGE_MARKERS = (
    "checking your browser", "enable javascript", "captcha", "recaptcha",
    "just a moment", "access denied", "403 forbidden", "cloudflare",
    "request unsuccessful", "are you a robot", "verifying you are human",
)
# Below this, a parsed "document" has no usable rotation/relationship evidence.
_MIN_USABLE_TEXT = 200


def _is_low_value_text(text: str) -> bool:
    stripped = (text or "").strip()
    if len(stripped) < _MIN_USABLE_TEXT:
        return True
    if len(stripped) < 1500 and any(m in stripped.lower() for m in _CHALLENGE_MARKERS):
        return True
    return False


def _looks_like_html(content: bytes) -> bool:
    return content[:512].lstrip()[:15].lower().startswith((b"<!doctype", b"<html"))


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
    # Salvage: a /pdf endpoint or `application/pdf` header that actually returned an
    # HTML challenge/error page must be parsed as HTML, not fed to pdftotext.
    if dtype == "pdf" and _looks_like_html(resp.content):
        dtype = "html"
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
    # Empty parses and bot-challenge pages are failures, not full-text documents.
    if _is_low_value_text(parsed.get("raw_text", "")):
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
    known_tiers = set(tier_rank_index(repo_root).keys())
    for f in sorted(claims_dir.glob("*.json")):
        payload = json.loads(f.read_text(encoding="utf-8"))
        for claim in payload.get("claims", []):
            try:
                registry.validate("crop-relationship-claim.schema.json", claim)
            except Exception as exc:
                invalid.append("{0}: {1}".format(f.name, str(exc)[:120]))
                continue
            # Tier enforcement: source_tier_id is schema-required, but a value not
            # in the manifest (typo / retired tier) must not silently rank as
            # worst — drop it with a clear reason so tier-aware resolution is safe.
            tier = (claim.get("provenance") or {}).get("source_tier_id", "")
            if tier not in known_tiers:
                invalid.append("{0}: unknown source_tier_id '{1}'".format(f.name, tier))
                continue
            claims.append(claim)
    # Code-enforced canonicalization for symmetric modes (intercrop, strip_crop,
    # mixed_crop, companion_crop): never trust the extractor's emitted endpoint
    # order. Both matrix population and the graph read claims through here.
    directionality = mode_directionality_map(load_relationship_vocabulary(repo_root))
    normalize_symmetric_claims(claims, directionality)
    return {"run_id": run_id, "claims": claims, "valid_count": len(claims), "invalid": invalid}


# --------------------------------------------------------------------------- #
# Tier-weighted effect resolution (shared by matrix population and the resolver)
# --------------------------------------------------------------------------- #
# Polarity classes over the relationship `effect` enum. Positive and negative
# are the decisive poles; `conditional` means context-dependent ("it depends");
# `neutral` is a weak non-conflicting signal; `unknown` is ignored entirely.
_EFFECT_POLARITY = {
    "beneficial": "positive",
    "compatible": "positive",
    "incompatible": "negative",
    "avoid": "negative",
    "conditional": "conditional",
    "neutral": "neutral",
    "unknown": "ignore",
}


def _claim_tier(claim: Dict[str, Any]) -> str:
    return (claim.get("provenance") or {}).get("source_tier_id", "") or ""


def _effect_polarity(effect: Optional[str]) -> str:
    return _EFFECT_POLARITY.get(effect or "", "ignore")


def tiered_effect(claims: List[Dict[str, Any]], rank_index: Dict[str, int]) -> Dict[str, Any]:
    """Resolve a set of claims for one (pair, mode) cell into a tier-weighted
    summary.

    The DECIDING tier is the most-trusted tier (lowest priority number) that
    actually carries a usable effect — a higher tier whose claims are all
    ``unknown`` does not mask a lower tier's real signal, nor set the grade. The
    deciding tier's effect/grade win; any other-tier claim whose decisive polarity
    is overridden (including when the summary is a non-committal
    ``conditional``/``neutral``/``unknown``) raises ``tier_superseded_conflict``
    so the disagreement is always reviewable.

    Returns: summary_effect, status, best_source_tier, evidence_grade,
    tier_histogram, ambiguous_effect, tier_superseded_conflict, evidence_count,
    conflict_count.
    """
    base = {
        "summary_effect": "unknown",
        "status": "searched_no_evidence",
        "best_source_tier": "",
        "evidence_grade": "none",
        "tier_histogram": {},
        "ambiguous_effect": False,
        "tier_superseded_conflict": False,
        "evidence_count": 0,
        "conflict_count": 0,
    }
    if not claims:
        return base

    by_rank: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for c in claims:
        by_rank[tier_rank(_claim_tier(c), rank_index)].append(c)

    deciding_rank = next(
        (r for r in sorted(by_rank)
         if any(_effect_polarity(c.get("effect")) != "ignore" for c in by_rank[r])),
        None,
    )
    if deciding_rank is None:
        # No claim anywhere carries a usable effect: report the best tier present
        # with an unknown effect.
        deciding_rank = min(by_rank)
        deciding = by_rank[deciding_rank]
        summary, status, ambiguous, conflict_count = "unknown", "evidence_found", False, 0
    else:
        deciding = by_rank[deciding_rank]
        effects = [c.get("effect") for c in deciding if _effect_polarity(c.get("effect")) != "ignore"]
        polarities = [_effect_polarity(e) for e in effects]
        decisive_effects = [e for e in effects if _effect_polarity(e) in ("positive", "negative")]
        positives = polarities.count("positive")
        negatives = polarities.count("negative")
        conditionals = polarities.count("conditional")
        if positives > 0 and negatives > 0:
            summary = Counter(decisive_effects).most_common(1)[0][0]
            status = "conflicting_evidence"
            summary_pol = _effect_polarity(summary)
            # Dissenting CLAIMS (opposite decisive polarity), not distinct labels.
            conflict_count = sum(1 for p in polarities if p in ("positive", "negative") and p != summary_pol)
            ambiguous = False
        elif conditionals > 0 and (positives > 0 or negatives > 0):
            # Decisive evidence coexists with "it depends" — surface the ambiguity
            # rather than letting a plurality bury the conditional caveat.
            summary = "conditional"
            status = "evidence_found"
            conflict_count = 0
            ambiguous = True
        elif decisive_effects:
            # One decisive polarity present: prefer it so a weak `neutral` never
            # ties out a `beneficial`/`avoid` on claim order.
            summary = Counter(decisive_effects).most_common(1)[0][0]
            status = "evidence_found"
            conflict_count = 0
            ambiguous = False
        else:
            # Only conditional/neutral at the deciding tier.
            summary = Counter(effects).most_common(1)[0][0]
            status = "evidence_found"
            conflict_count = 0
            ambiguous = False

    # Grade and best tier follow the DECIDING tier (the one that set the effect),
    # not merely the numerically best tier present.
    best_source_tier = sorted({_claim_tier(c) for c in deciding})[0]
    grade = "peer_reviewed" if tier_band(best_source_tier) == "evidence" else "reference_backbone"

    # Raise the supersede flag whenever a non-deciding claim's decisive polarity
    # is overridden — including under a non-committal summary.
    summary_pol = _effect_polarity(summary)
    others = [c for r, group in by_rank.items() if r != deciding_rank for c in group]
    other_decisive = {
        _effect_polarity(c.get("effect"))
        for c in others
        if _effect_polarity(c.get("effect")) in ("positive", "negative")
    }
    if summary_pol == "positive":
        tier_superseded_conflict = "negative" in other_decisive
    elif summary_pol == "negative":
        tier_superseded_conflict = "positive" in other_decisive
    else:
        tier_superseded_conflict = bool(other_decisive)

    return {
        "summary_effect": summary,
        "status": status,
        "best_source_tier": best_source_tier,
        "evidence_grade": grade,
        "tier_histogram": dict(Counter(_claim_tier(c) for c in claims)),
        "ambiguous_effect": ambiguous,
        "tier_superseded_conflict": tier_superseded_conflict,
        "evidence_count": len(claims),
        "conflict_count": conflict_count,
    }


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
    accepted = _dedupe_claims_by_id(
        [c for c in loaded["claims"] if c.get("status") in _EVIDENCE_STATUSES]
    )
    by_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in accepted:
        by_key[c["canonical_relationship_key"]].append(c)
    rank_index = tier_rank_index(repo_root)

    populated = 0
    for cell in matrix["cells"]:
        for mode_id, st in cell["mode_statuses"].items():
            # Tier-aware defaults on every cell so populated matrices are uniform.
            st.setdefault("best_source_tier", "")
            st.setdefault("tier_histogram", {})
            st.setdefault("evidence_grade", "none")
            st.setdefault("ambiguous_effect", False)
            st.setdefault("tier_superseded_conflict", False)
            ckey = st["canonical_relationship_key"]
            claims = by_key.get(ckey, [])
            if claims:
                resolved = tiered_effect(claims, rank_index)
                st["status"] = resolved["status"]
                st["summary_effect"] = resolved["summary_effect"]
                st["evidence_count"] = resolved["evidence_count"]
                st["conflict_count"] = resolved["conflict_count"]
                st["best_source_tier"] = resolved["best_source_tier"]
                st["tier_histogram"] = resolved["tier_histogram"]
                st["evidence_grade"] = resolved["evidence_grade"]
                st["ambiguous_effect"] = resolved["ambiguous_effect"]
                st["tier_superseded_conflict"] = resolved["tier_superseded_conflict"]
                populated += 1
            elif ckey in searched_keys:
                st["status"] = "searched_no_evidence"

    # Guard against future field/enum drift: the populated matrix carries the
    # tier-aware cell fields tiered_effect writes, so validate before persisting.
    from .schema_registry import SchemaRegistry
    SchemaRegistry(repo_root).validate("crop-relationship-matrix.schema.json", matrix)
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


def _dedupe_claims_by_id(claims: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop duplicate claims sharing a ``relationship_claim_id`` (first wins).
    Claims without an id are kept (they cannot collide). Centralized so the
    single-run graph, merged graph, and matrix paths all see the same evidence
    set and a duplicated id can never manufacture a phantom conflict."""
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for claim in claims:
        cid = claim.get("relationship_claim_id")
        if cid:
            if cid in seen:
                continue
            seen.add(cid)
        out.append(claim)
    return out


def _graph_from_claims(repo_root: Path, claims: List[Dict[str, Any]], run_label: str) -> Dict[str, Any]:
    """Index evidence-bearing claims into a resolver graph keyed by
    (relationship_mode, subject_tuple, object_tuple) so evidence for one mode
    never bleeds into another. Shared by the single-run and merged-run builders."""
    claims = _dedupe_claims_by_id(claims)
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

    return {
        "run_id": run_label,
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


def build_relationship_graph(repo_root: Path, run_id: str) -> Dict[str, Any]:
    """Build and persist an evidence graph from one run's validated claims."""
    loaded = validate_relationship_claims(repo_root, run_id)
    claims = [c for c in loaded["claims"] if c.get("status") in _EVIDENCE_STATUSES]
    graph = _graph_from_claims(repo_root, claims, run_id)
    out_dir = repo_root / "exploration" / "relationships" / "graph"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "{0}.json".format(run_id)).write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
    return graph


def build_merged_relationship_graph(
    repo_root: Path,
    run_ids: List[str],
    status_filter: Sequence[str] = _EVIDENCE_STATUSES,
    persist_label: Optional[str] = None,
) -> Dict[str, Any]:
    """Union evidence-bearing claims across runs into one resolver graph, so a
    peer-reviewed upgrade in run B can supersede a backbone claim in run A.
    Claims are deduped by ``relationship_claim_id`` in ``_graph_from_claims``
    (claims without one are kept, since they cannot collide identity-wise).
    ``status_filter`` lets callers build an accepted-only graph (A9) vs accepting
    provisional ``needs_review`` too."""
    merged: List[Dict[str, Any]] = []
    for run_id in run_ids:
        loaded = validate_relationship_claims(repo_root, run_id)
        merged.extend(c for c in loaded["claims"] if c.get("status") in status_filter)
    label = persist_label or "merged-{0}".format("+".join(run_ids))
    graph = _graph_from_claims(repo_root, merged, label)
    if persist_label is not None:
        out_dir = repo_root / "exploration" / "relationships" / "graph"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "{0}.json".format(label)).write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
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


def _apply_tiered(result: Dict[str, Any], resolved: Dict[str, Any]) -> None:
    """Copy a tiered_effect() summary onto a resolver result, surfacing a
    hard conflict as a status flag (the provenance path stays direct/inferred)."""
    result["primary_effect"] = resolved["summary_effect"]
    result["best_source_tier"] = resolved["best_source_tier"]
    result["evidence_grade"] = resolved["evidence_grade"]
    result["ambiguous_effect"] = resolved["ambiguous_effect"]
    result["tier_superseded_conflict"] = resolved["tier_superseded_conflict"]
    if resolved["status"] == "conflicting_evidence" and "conflicting_evidence" not in result["status_flags"]:
        result["status_flags"].append("conflicting_evidence")


def _resolve(
    graph: Dict[str, Any],
    catalog: Dict[str, Any],
    rank_index: Dict[str, int],
    subject: str,
    object: str,
    mode: str = "rotation",
) -> Dict[str, Any]:
    """Resolve a (subject, object) relationship for one mode against an already
    loaded graph. Checks exact crop evidence first, then group inference, always
    overlaying shared host-risk caveats. Effects are tier-weighted."""
    alias_index = _node_alias_index(catalog)
    result: Dict[str, Any] = {
        "run_id": graph.get("run_id", ""),
        "mode": mode,
        "subject": subject,
        "object": object,
        "status": "no_evidence",
        "primary_effect": "unknown",
        "inference_basis": "",
        "best_source_tier": "",
        "evidence_grade": "none",
        "ambiguous_effect": False,
        "tier_superseded_conflict": False,
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
        _apply_tiered(result, tiered_effect(direct_claims, rank_index))
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
                _apply_tiered(result, tiered_effect(matched, rank_index))
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


def resolve_crop_relationship(
    repo_root: Path,
    run_id: str,
    subject: str,
    object: str,
    mode: str = "rotation",
) -> Dict[str, Any]:
    """Resolve a (subject, object) crop relationship for one mode from one run's
    persisted graph. (See ``relationship_coverage_report`` for the cross-run,
    tier-aware merged view.)"""
    catalog = load_node_catalog(repo_root)
    graph = _load_relationship_graph(repo_root, run_id)
    rank_index = tier_rank_index(repo_root)
    return _resolve(graph, catalog, rank_index, subject, object, mode)


# --------------------------------------------------------------------------- #
# Coverage report — cross-run, tier-aware answerability + evidence grade
# --------------------------------------------------------------------------- #
_ANSWERABLE_STATUSES = ("direct_evidence", "inferred_from_group")
_GRADE_RANK = {"none": 0, "reference_backbone": 1, "peer_reviewed": 2}


def _best_pair_resolution(graph, catalog, rank_index, s_id, o_id, mode, directional):
    """Resolve a crop pair for one mode, taking the better-graded direction for
    directional modes (the coverage denominator is unordered pairs, so a pair is
    answerable if either direction has evidence)."""
    cands = [_resolve(graph, catalog, rank_index, s_id, o_id, mode)]
    if directional:
        cands.append(_resolve(graph, catalog, rank_index, o_id, s_id, mode))
    answerable = [c for c in cands if c["status"] in _ANSWERABLE_STATUSES]
    if not answerable:
        return cands[0]
    return max(answerable, key=lambda c: _GRADE_RANK.get(c["evidence_grade"], 0))


def _directed_resolutions(graph, catalog, rank_index, s_id, o_id, mode, directional):
    """Yield (directed_key, result) per relevant direction. Directional modes get
    both orders so a pair that is peer-reviewed one way but backbone-only the other
    surfaces the backbone direction as its own upgrade target."""
    yield ("{0}|{1}".format(s_id, o_id), _resolve(graph, catalog, rank_index, s_id, o_id, mode))
    if directional:
        yield ("{0}|{1}".format(o_id, s_id), _resolve(graph, catalog, rank_index, o_id, s_id, mode))


def relationship_coverage_report(
    repo_root: Path,
    run_ids: List[str],
    modes: Sequence[str] = ("rotation", "intercrop"),
    crop_dir: str = "config/crops",
    persist_label: Optional[str] = None,
) -> Dict[str, Any]:
    """Cross-run, tier-aware coverage. Builds a merged claim graph across all
    ``run_ids`` and resolves every distinct (no-self) crop pair × mode against it.

    Headline coverage is **accepted-backed**; ``needs_review`` evidence is
    reported separately as ``provisional`` (A9) — it inflates answerability but is
    not yet reviewed. Per mode it reports the evidence-grade split
    (peer_reviewed / reference_backbone / none), the **upgrade candidates**
    (answerable at backbone grade), and any tier-superseded / ambiguous cells."""
    catalog = load_node_catalog(repo_root)
    rank_index = tier_rank_index(repo_root)
    directionality = mode_directionality_map(load_relationship_vocabulary(repo_root))

    graph_accepted = build_merged_relationship_graph(repo_root, run_ids, status_filter=("accepted",))
    graph_provisional = build_merged_relationship_graph(repo_root, run_ids, status_filter=_EVIDENCE_STATUSES)

    crops = load_crop_universe(repo_root, crop_dir)
    pairs = unordered_crop_pairs(crops, include_self_pairs=False)
    total = len(pairs)

    # Crops in the universe that have no node in the catalog resolve to
    # `unknown_nodes` and would otherwise be silently bucketed as `none`. Surface
    # them so a crop-config / node-catalog drift is visible, not invisible.
    alias_index = _node_alias_index(catalog)
    unknown_crops = sorted(c.crop_id for c in crops if c.crop_id.strip().lower() not in alias_index)

    modes_detail: Dict[str, Any] = {}
    for mode in modes:
        directional = directionality.get(mode) != "symmetric"

        def _split(graph):
            grades = Counter()
            for subject, obj in pairs:
                res = _best_pair_resolution(
                    graph, catalog, rank_index, subject.crop_id, obj.crop_id, mode, directional
                )
                grade = res["evidence_grade"] if res["status"] in _ANSWERABLE_STATUSES else "none"
                grades[grade] += 1
            answerable = grades["peer_reviewed"] + grades["reference_backbone"]
            return {
                "answerable": answerable,
                "peer_reviewed": grades["peer_reviewed"],
                "reference_backbone": grades["reference_backbone"],
                "none": grades["none"],
            }

        # Upgrade candidates and flags are reported per DIRECTION for directional
        # modes (keys are directed), so a backbone-only direction is never hidden
        # behind a peer-reviewed reverse direction.
        upgrade_candidates: List[str] = []
        superseded: List[str] = []
        ambiguous: List[str] = []
        for subject, obj in pairs:
            for key, res in _directed_resolutions(
                graph_accepted, catalog, rank_index, subject.crop_id, obj.crop_id, mode, directional
            ):
                if res["status"] in _ANSWERABLE_STATUSES and res["evidence_grade"] == "reference_backbone":
                    upgrade_candidates.append(key)
                if res.get("tier_superseded_conflict"):
                    superseded.append(key)
                if res.get("ambiguous_effect"):
                    ambiguous.append(key)

        modes_detail[mode] = {
            "directional": directional,
            "accepted": _split(graph_accepted),
            "provisional": _split(graph_provisional),
            "upgrade_candidates": sorted(upgrade_candidates),
            "tier_superseded_conflicts": sorted(superseded),
            "ambiguous_effects": sorted(ambiguous),
        }

    report = {
        "run_ids": list(run_ids),
        "modes": list(modes),
        "generated_at": current_time(),
        "crop_count": len(crops),
        "total_pairs": total,
        "unknown_crops": unknown_crops,
        "merged_claim_count": graph_provisional["claim_count"],
        "accepted_claim_count": graph_accepted["claim_count"],
        "modes_detail": modes_detail,
    }
    label = persist_label or "+".join(run_ids)
    out_dir = repo_root / "exploration" / "relationships" / "coverage"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "coverage-{0}.json".format(label)).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


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
