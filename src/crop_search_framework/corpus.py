"""Durable, deduplicated raw corpus layer.

Transforms the per-(url, parameter) capture explosion produced by
``run-exploration`` into:

* an immutable, content-addressed **document registry** (one record per unique
  document, deduped by text hash),
* a **block store** (sections / paragraphs / tables with anchors and offsets),
* **query_hits** associations linking each document to the parameters/tiers/
  queries that surfaced it,
* a **corpus_manifest** snapshot (run, manifest version, parser version, hashes),
* a **QA report** that gates the expensive Opus extraction pass.

This module reads the existing raw captures (it does not crawl). The live
discovery ledger (all provider results with ranks/scores/drop-reasons) and the
provider retry/backoff/OA-resolver layer are a separate crawl-side increment;
this builds the durable document/block store + QA report from what is on disk.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:  # bs4 is already a project dependency (search_web / parse_document)
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - import guard
    BeautifulSoup = None  # type: ignore


PARSER_VERSION = "corpus-blocks-1"
CORPUS_VERSION = "0.1.0"

# Heuristic thresholds for QA.
SHORT_TEXT_CHARS = 500
BACKGROUND_DOMAINS = ("en.wikipedia.org", "wikipedia.org")


# --------------------------------------------------------------------------- #
# Hashing / ids
# --------------------------------------------------------------------------- #
def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def text_hash(text: str) -> str:
    norm = _normalize_text(text)
    if not norm:
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    """sha256 of a config file, or '' if absent (policy fingerprint)."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def content_hash_of(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return ""


def fallback_key(source_url: str, title: str) -> str:
    return hashlib.sha256(("{0}|{1}".format(source_url, title)).encode("utf-8")).hexdigest()


def document_id_for(key: str) -> str:
    return "doc-{0}".format(key[:16])


# --------------------------------------------------------------------------- #
# Block extraction
# --------------------------------------------------------------------------- #
def paragraphs_from_text(raw_text: str) -> List[Dict[str, Any]]:
    """Segment raw text into paragraph blocks with running char offsets."""
    blocks: List[Dict[str, Any]] = []
    offset = 0
    for chunk in re.split(r"\n{2,}", raw_text or ""):
        piece = chunk.strip()
        start = (raw_text or "").find(piece, offset) if piece else -1
        if len(piece) >= 40:
            blocks.append({"type": "paragraph", "text": piece, "char_offset": max(start, 0)})
        offset = start + len(piece) if start >= 0 else offset
    return blocks


def blocks_from_html(artifact_path: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (sections, tables) extracted from an HTML artifact."""
    if BeautifulSoup is None or not artifact_path.exists():
        return [], []
    try:
        soup = BeautifulSoup(artifact_path.read_text(encoding="utf-8", errors="ignore"), "lxml")
    except Exception:
        return [], []
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    sections: List[Dict[str, Any]] = []
    for idx, heading in enumerate(soup.find_all(["h1", "h2", "h3", "h4"])):
        title = _normalize_text(heading.get_text(" ", strip=True))
        if not title:
            continue
        body_parts: List[str] = []
        for sib in heading.find_next_siblings():
            if sib.name in ("h1", "h2", "h3", "h4"):
                break
            if sib.name in ("p", "ul", "ol", "div"):
                body_parts.append(_normalize_text(sib.get_text(" ", strip=True)))
        sections.append(
            {
                "type": "section",
                "anchor": "sec-{0}".format(idx),
                "level": int(heading.name[1]),
                "heading": title,
                "text": " ".join(p for p in body_parts if p)[:4000],
            }
        )

    tables: List[Dict[str, Any]] = []
    for idx, table in enumerate(soup.find_all("table")):
        rows = []
        for tr in table.find_all("tr"):
            cells = [_normalize_text(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
            if any(cells):
                rows.append(cells)
        if len(rows) < 2:
            continue
        caption_node = table.find("caption")
        tables.append(
            {
                "type": "table",
                "anchor": "tbl-{0}".format(idx),
                "caption": _normalize_text(caption_node.get_text(" ", strip=True)) if caption_node else "",
                "headers": rows[0],
                "rows": rows[1:],
            }
        )
    return sections, tables


def build_blocks(capture: Dict[str, Any], repo_root: Path) -> Dict[str, Any]:
    raw_text = capture.get("raw_text", "")
    sections: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    artifact_rel = capture.get("artifact_path", "")
    if capture.get("document_type") == "html" and artifact_rel:
        sections, tables = blocks_from_html(repo_root / artifact_rel)
    return {
        "parser_version": PARSER_VERSION,
        "sections": sections,
        "tables": tables,
        "paragraphs": paragraphs_from_text(raw_text),
        "labels": _block_labels(sections, tables),
    }


def _block_labels(sections: List[Dict[str, Any]], tables: List[Dict[str, Any]]) -> List[str]:
    labels = set()
    text = " ".join((s.get("heading", "") + " " + s.get("caption", "")) for s in sections).lower()
    text += " ".join(t.get("caption", "") + " " + " ".join(t.get("headers", [])) for t in tables).lower()
    keywords = {
        "nutrient_table": ("nitrogen", "phosphorus", "potassium", "fertil", "nutrient", "n rate"),
        "growth_stage_table": ("growth stage", "bbch", "zadoks", "feekes", "phenolog"),
        "yield_table": ("yield", "t/ha", "bu/ac", "kg/ha"),
        "temperature_table": ("temperature", "°c", "degrees"),
    }
    if tables:
        labels.add("has_table")
        for label, terms in keywords.items():
            if any(term in text for term in terms):
                labels.add(label)
    return sorted(labels)


# --------------------------------------------------------------------------- #
# Corpus build
# --------------------------------------------------------------------------- #
def _load_captures(raw_dir: Path) -> List[Dict[str, Any]]:
    captures = []
    for path in sorted(raw_dir.glob("*.json")):
        if path.name == "summary.json":
            continue
        try:
            captures.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return captures


def build_corpus(repo_root: Path, run_id: str, source_run: Optional[str] = None) -> Dict[str, Any]:
    source_run = source_run or run_id
    raw_dir = repo_root / "exploration" / "raw" / source_run
    if not raw_dir.exists():
        raise FileNotFoundError("no raw captures at {0}".format(raw_dir))

    out_dir = repo_root / "exploration" / "corpus" / run_id
    docs_dir = out_dir / "documents"
    blocks_dir = out_dir / "blocks"
    blobs_dir = docs_dir / "blobs"
    for d in (docs_dir, blocks_dir, blobs_dir):
        d.mkdir(parents=True, exist_ok=True)

    summary = _load_run_summary(raw_dir)
    crop = summary.get("crop", "")
    manifest_version = _manifest_version(repo_root)

    captures = _load_captures(raw_dir)
    documents: Dict[str, Dict[str, Any]] = {}
    query_hits: List[Dict[str, Any]] = []

    for capture in captures:
        raw_text = capture.get("raw_text", "")
        th = text_hash(raw_text)
        is_metadata_only = (capture.get("access_status") == "metadata_only") or not th
        key = th or fallback_key(capture.get("source_url", ""), capture.get("source_title", ""))
        doc_id = document_id_for(key)

        if doc_id not in documents:
            artifact_rel = capture.get("artifact_path", "")
            ch = content_hash_of(repo_root / artifact_rel) if artifact_rel else ""
            documents[doc_id] = {
                "document_id": doc_id,
                "canonical_url": capture.get("final_url") or capture.get("source_url", ""),
                "source_url": capture.get("source_url", ""),
                "doi": (capture.get("source_metadata") or {}).get("doi", ""),
                "title": capture.get("source_title", ""),
                "source_domain": capture.get("source_domain", ""),
                "source_tier_id": capture.get("source_tier_id", ""),
                "discovery_method": capture.get("discovery_method", ""),
                "access_status": capture.get("access_status", ""),
                "document_type": capture.get("document_type", ""),
                "content_type": capture.get("content_type", ""),
                "is_metadata_only": is_metadata_only,
                "text_hash": th,
                "content_hash": ch,
                "text_length": len(_normalize_text(raw_text)),
                "publication_date_hint": capture.get("publication_date_hint", ""),
                "source_metadata": capture.get("source_metadata", {}),
                "artifact_path": artifact_rel,
                "parser_version": PARSER_VERSION,
            }
            # Block store + immutable raw_text snapshot (content-addressed name).
            blocks = build_blocks(capture, repo_root)
            blocks["document_id"] = doc_id
            (blocks_dir / "{0}.json".format(doc_id)).write_text(
                json.dumps(blocks, indent=2) + "\n", encoding="utf-8"
            )
            (docs_dir / "{0}.json".format(doc_id)).write_text(
                json.dumps(documents[doc_id], indent=2) + "\n", encoding="utf-8"
            )
            (blobs_dir / "{0}.txt".format(doc_id)).write_text(raw_text or "", encoding="utf-8")

        query_hits.append(
            {
                "document_id": doc_id,
                "capture_id": capture.get("id", ""),
                "parameter_id": capture.get("parameter_id", ""),
                "parameter_family": capture.get("parameter_family", ""),
                "source_tier_id": capture.get("source_tier_id", ""),
                "query": capture.get("query", ""),
                "discovery_method": capture.get("discovery_method", ""),
            }
        )

    (out_dir / "query_hits.jsonl").write_text(
        "".join(json.dumps(h) + "\n" for h in query_hits), encoding="utf-8"
    )

    # Reproducibility fingerprint over the deduped document set (WS-7): any
    # change in content or parsing changes this hash.
    corpus_content_hash = hashlib.sha256(
        "\n".join(
            "{0}|{1}|{2}".format(doc_id, documents[doc_id].get("text_hash", ""), documents[doc_id].get("content_hash", ""))
            for doc_id in sorted(documents)
        ).encode("utf-8")
    ).hexdigest()

    # Durable versioning: a rerun that changes content bumps the revision and
    # records the prior fingerprint rather than silently overwriting history.
    manifest_path = out_dir / "corpus_manifest.json"
    revision, previous_hash = 1, ""
    if manifest_path.exists():
        try:
            prev = json.loads(manifest_path.read_text(encoding="utf-8"))
            previous_hash = prev.get("corpus_content_hash", "")
            revision = int(prev.get("manifest_revision", 1)) + (1 if previous_hash != corpus_content_hash else 0)
        except Exception:
            pass

    manifest = {
        "corpus_version": CORPUS_VERSION,
        "run_id": run_id,
        "source_run": source_run,
        "crop": crop,
        "manifest_version": manifest_version,
        "parser_version": PARSER_VERSION,
        "hashing": "sha256",
        "created_at": _now_iso(),
        "manifest_revision": revision,
        "corpus_content_hash": corpus_content_hash,
        "previous_corpus_content_hash": previous_hash,
        "policy_hashes": {
            "source_tier_policy": _file_hash(repo_root / "config/source-tiers/default.json"),
            "fetch_policy": _file_hash(repo_root / "config/fetch-policy/default.json"),
            "query_templates": _file_hash(repo_root / "config/query-templates/default.json"),
            "parameter_manifest": _file_hash(repo_root / "config/parameters/core-crop-parameters.json"),
        },
        "capture_count": len(captures),
        "document_count": len(documents),
        "query_hit_count": len(query_hits),
        "raw_summary_failure_count": summary.get("failure_count", 0),
        "document_index": sorted(documents.keys()),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return {
        "run_id": run_id,
        "source_run": source_run,
        "captures": len(captures),
        "unique_documents": len(documents),
        "query_hits": len(query_hits),
        "output_dir": str(out_dir.relative_to(repo_root)),
    }


def _load_run_summary(raw_dir: Path) -> Dict[str, Any]:
    path = raw_dir / "summary.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _manifest_version(repo_root: Path) -> str:
    path = repo_root / "config" / "parameters" / "core-crop-parameters.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("manifest_version", "")
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# QA report (gates full Opus)
# --------------------------------------------------------------------------- #
def corpus_qa(repo_root: Path, run_id: str) -> Dict[str, Any]:
    out_dir = repo_root / "exploration" / "corpus" / run_id
    manifest = json.loads((out_dir / "corpus_manifest.json").read_text(encoding="utf-8"))
    docs = [json.loads(p.read_text(encoding="utf-8")) for p in (out_dir / "documents").glob("doc-*.json")]
    hits = [json.loads(line) for line in (out_dir / "query_hits.jsonl").read_text(encoding="utf-8").splitlines() if line]

    # The Opus input set excludes documents the backfill flagged as non-articles
    # (junk DOIs) or unrecoverable metadata-only records.
    opus_docs = [d for d in docs if not d.get("excluded_from_opus")]
    excluded = [d for d in docs if d.get("excluded_from_opus")]
    total_docs = len(opus_docs)
    all_docs_count = len(docs)
    capture_count = manifest.get("capture_count", 0)
    text_docs = [d for d in opus_docs if d.get("text_hash")]
    metadata_only = [d for d in opus_docs if d.get("is_metadata_only")]
    background = [d for d in opus_docs if d.get("source_domain") in BACKGROUND_DOMAINS]
    short_docs = [d for d in opus_docs if not d.get("is_metadata_only") and d.get("text_length", 0) < SHORT_TEXT_CHARS]
    backfilled = [d for d in opus_docs if d.get("backfilled")]

    # capture_redundancy_collapsed: how much the (url,param) capture explosion was
    # collapsed by dedup (informational — higher is better, this is the win).
    capture_redundancy_collapsed = round(1 - (all_docs_count / capture_count), 3) if capture_count else 0.0
    # duplicate_text_ratio: duplicates REMAINING in the Opus input set (unique docs).
    # After text-hash dedup this is ~0 by construction; the gate guards regressions.
    distinct_text = len({d.get("text_hash") for d in text_docs})
    dup_ratio = round(1 - (distinct_text / len(text_docs)), 3) if text_docs else 0.0

    # table coverage from block store (Opus input set only)
    opus_ids = {d["document_id"] for d in opus_docs}
    docs_with_tables = 0
    for p in (out_dir / "blocks").glob("doc-*.json"):
        if p.stem not in opus_ids:
            continue
        b = json.loads(p.read_text(encoding="utf-8"))
        if b.get("tables"):
            docs_with_tables += 1

    tier_doc_counts = Counter(d.get("source_tier_id", "") for d in opus_docs)
    domain_counts = Counter(d.get("source_domain", "") for d in opus_docs)

    # parameters with no (non-excluded) document → retry candidates
    params_with_docs = {h["parameter_id"] for h in hits if h["document_id"] in opus_ids}
    all_params = _active_parameter_ids(repo_root)
    params_missing = sorted(set(all_params) - params_with_docs)

    def share(items: List[Any]) -> float:
        return round(len(items) / total_docs, 3) if total_docs else 0.0

    gates = {
        "duplicate_text_ratio_lt_0.10": dup_ratio < 0.10,
        "metadata_only_share_lt_0.15": share(metadata_only) < 0.15,
        "background_share_lt_0.15": share(background) < 0.15,
        "has_tables": docs_with_tables > 0,
    }

    report = {
        "run_id": run_id,
        "capture_count": capture_count,
        "all_document_count": all_docs_count,
        "opus_input_document_count": total_docs,
        "unique_document_count": total_docs,
        "excluded_from_opus_count": len(excluded),
        "backfilled_full_text_count": len(backfilled),
        "documents_with_text": len(text_docs),
        "capture_redundancy_collapsed": capture_redundancy_collapsed,
        "duplicate_text_ratio": dup_ratio,
        "metadata_only_count": len(metadata_only),
        "metadata_only_share": share(metadata_only),
        "background_count": len(background),
        "background_share": share(background),
        "short_text_doc_count": len(short_docs),
        "documents_with_tables": docs_with_tables,
        "raw_summary_failure_count": manifest.get("raw_summary_failure_count", 0),
        "source_tier_doc_counts": dict(tier_doc_counts),
        "top_domains": dict(domain_counts.most_common(15)),
        "parameters_with_documents": len(params_with_docs),
        "parameters_missing_documents": len(params_missing),
        "high_value_retry_queue": params_missing,
        "gates": gates,
        "gates_passed": all(gates.values()),
    }
    (out_dir / "qa_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (out_dir / "qa_report.md").write_text(_render_qa_md(report), encoding="utf-8")
    return report


def _render_qa_md(r: Dict[str, Any]) -> str:
    lines = [
        "# Raw Corpus QA Report — {0}".format(r["run_id"]),
        "",
        "| Metric | Value |",
        "|---|---|",
        "| Captures | {0} |".format(r["capture_count"]),
        "| Unique documents | {0} |".format(r["unique_document_count"]),
        "| Documents with text | {0} |".format(r["documents_with_text"]),
        "| Capture redundancy collapsed | {0} |".format(r["capture_redundancy_collapsed"]),
        "| Duplicate text ratio (Opus input) | {0} |".format(r["duplicate_text_ratio"]),
        "| Metadata-only | {0} ({1}) |".format(r["metadata_only_count"], r["metadata_only_share"]),
        "| Background (Wikipedia) | {0} ({1}) |".format(r["background_count"], r["background_share"]),
        "| Short-text docs | {0} |".format(r["short_text_doc_count"]),
        "| Docs with tables | {0} |".format(r["documents_with_tables"]),
        "| Raw fetch failures | {0} |".format(r["raw_summary_failure_count"]),
        "| Params with documents | {0} |".format(r["parameters_with_documents"]),
        "| Params missing documents | {0} |".format(r["parameters_missing_documents"]),
        "",
        "## Gates",
        "",
    ]
    for gate, ok in r["gates"].items():
        lines.append("- [{0}] {1}".format("x" if ok else " ", gate))
    lines.append("")
    lines.append("**Gates passed: {0}**".format(r["gates_passed"]))
    lines.append("")
    lines.append("## High-value retry queue (parameters with zero documents)")
    lines.append("")
    for pid in r["high_value_retry_queue"]:
        lines.append("- {0}".format(pid))
    return "\n".join(lines) + "\n"


def _active_parameter_ids(repo_root: Path) -> List[str]:
    path = repo_root / "config" / "parameters" / "core-crop-parameters.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [
        p["parameter_id"]
        for p in manifest.get("parameters", [])
        if p.get("implementation_status", "active") == "active"
    ]
