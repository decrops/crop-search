"""Phase 1.5 — corpus backfill: turn metadata-only records into open full text.

Two-pronged, run after ``build-corpus``:

1. **OA resolution + fetch** — for metadata-only documents with a DOI, resolve an
   open-access full-text URL (Unpaywall, then OpenAlex ``best_oa_location``) and
   fetch+parse it, flipping the document to ``open_full_text`` with real blocks.
2. **Junk exclusion** — DOIs that are supplements, peer-review stubs, datasets, or
   F1000 recommendations are not articles; they are marked ``excluded_from_opus``
   so they neither count as metadata-only candidates nor get sent to Opus.

Provider calls go through ``http_get_with_retry`` (exponential backoff on 429/5xx
+ a small on-disk cache). Results are written back into the corpus document/block
store and a ``backfill_report.json`` is produced; re-run ``corpus-qa`` afterwards.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from . import corpus
from .tool_runner import CommandToolRunner

UNPAYWALL_URL = "https://api.unpaywall.org/v2/"
OPENALEX_WORK_URL = "https://api.openalex.org/works/https://doi.org/"

# DOI shapes that are not full articles → exclude from the Opus input set.
JUNK_DOI_PATTERNS = (
    r"/supp", r"/table-", r"/fig-", r"/reviews?/", r"/submission",
    r"^10\.3410/",   # F1000 recommendations
    r"^10\.3974/",   # GeoDB datasets
)


def is_junk_doi(doi: str) -> bool:
    d = (doi or "").lower()
    return any(re.search(p, d) for p in JUNK_DOI_PATTERNS)


# --------------------------------------------------------------------------- #
# hardened HTTP
# --------------------------------------------------------------------------- #
def http_get_with_retry(
    url: str, *, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None,
    timeout: int = 20, max_retries: int = 4, cache_dir: Optional[Path] = None,
    sleeper=time.sleep,
) -> Optional[requests.Response]:
    """GET with exponential backoff on 429/5xx and an optional on-disk cache.

    Returns the Response on success, or None if all retries are exhausted.
    """
    cache_path = None
    if cache_dir is not None:
        key = hashlib.sha256(("{0}|{1}".format(url, json.dumps(params, sort_keys=True))).encode()).hexdigest()
        cache_path = cache_dir / "{0}.json".format(key[:24])
        if cache_path.exists():
            return _CachedResponse(json.loads(cache_path.read_text(encoding="utf-8")))

    delay = 1.0
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException:
            sleeper(delay); delay *= 2; continue
        if resp.status_code == 429 or resp.status_code >= 500:
            sleeper(delay); delay *= 2; continue
        if cache_path is not None and resp.status_code == 200:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(resp.json()), encoding="utf-8")
            except Exception:
                pass
        return resp
    return None


class _CachedResponse:
    """Minimal Response stand-in for cache hits."""
    status_code = 200

    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        return None


# --------------------------------------------------------------------------- #
# OA resolution
# --------------------------------------------------------------------------- #
def resolve_oa(doi: str, email: str, user_agent: str, cache_dir: Optional[Path] = None) -> Optional[Dict[str, str]]:
    """Return {'url':..., 'resolver':...} for an open full-text location, or None."""
    if not doi:
        return None
    # Unpaywall first (best coverage for OA full text).
    resp = http_get_with_retry(
        UNPAYWALL_URL + doi, params={"email": email}, cache_dir=cache_dir, timeout=20
    )
    if resp is not None and resp.status_code == 200:
        try:
            loc = (resp.json() or {}).get("best_oa_location") or {}
            url = loc.get("url_for_pdf") or loc.get("url")
            if url:
                return {"url": url, "resolver": "unpaywall"}
        except Exception:
            pass
    # OpenAlex best_oa_location fallback.
    resp = http_get_with_retry(
        OPENALEX_WORK_URL + doi, headers={"User-Agent": user_agent}, cache_dir=cache_dir, timeout=20
    )
    if resp is not None and resp.status_code == 200:
        try:
            loc = (resp.json() or {}).get("best_oa_location") or {}
            url = loc.get("pdf_url") or loc.get("landing_page_url")
            if url:
                return {"url": url, "resolver": "openalex"}
        except Exception:
            pass
    return None


# --------------------------------------------------------------------------- #
# backfill driver
# --------------------------------------------------------------------------- #
def backfill_corpus(
    repo_root: Path, run_id: str, email: str = "research@example.org",
    limit: Optional[int] = None, manifest_path: str = "config/mcp/servers.local.json",
) -> Dict[str, Any]:
    out_dir = repo_root / "exploration" / "corpus" / run_id
    docs_dir = out_dir / "documents"
    blocks_dir = out_dir / "blocks"
    blobs_dir = docs_dir / "blobs"
    cache_dir = repo_root / "exploration" / "cache" / "oa"

    manifest = json.loads((out_dir / "corpus_manifest.json").read_text(encoding="utf-8"))
    crop = manifest.get("crop", "")
    user_agent = "crop-search/0.1 (mailto:{0})".format(email)
    tools = CommandToolRunner(repo_root, repo_root / manifest_path)

    doc_paths = sorted(docs_dir.glob("doc-*.json"))
    candidates = []
    for p in doc_paths:
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("is_metadata_only") and not d.get("excluded_from_opus"):
            candidates.append((p, d))

    report = {
        "run_id": run_id, "metadata_only_candidates": len(candidates),
        "excluded_junk": 0, "excluded_no_oa": 0, "resolved": 0, "fetched_full_text": 0,
        "fetch_failed": 0, "details": [],
    }

    processed = 0
    for path, doc in candidates:
        if limit is not None and processed >= limit:
            break
        processed += 1
        doi = doc.get("doi", "")
        doc_id = doc["document_id"]

        if not doi or is_junk_doi(doi):
            doc["excluded_from_opus"] = True
            doc["exclusion_reason"] = "junk_doi" if doi else "no_doi"
            report["excluded_junk"] += 1
            _save(path, doc)
            report["details"].append({"document_id": doc_id, "doi": doi, "outcome": doc["exclusion_reason"]})
            continue

        oa = resolve_oa(doi, email, user_agent, cache_dir=cache_dir)
        if not oa:
            doc["excluded_from_opus"] = True
            doc["exclusion_reason"] = "no_oa_full_text"
            report["excluded_no_oa"] += 1
            _save(path, doc)
            report["details"].append({"document_id": doc_id, "doi": doi, "outcome": "no_oa"})
            continue

        report["resolved"] += 1
        text, artifact_rel = _fetch_full_text(tools, run_id, crop, oa["url"], doc)
        if not text:
            report["fetch_failed"] += 1
            report["details"].append({"document_id": doc_id, "doi": doi, "outcome": "fetch_failed", "oa_url": oa["url"]})
            _save(path, doc)
            continue

        # update document + block store + blob in place (explicit backfill augmentation)
        doc.update({
            "access_status": "open_full_text",
            "is_metadata_only": False,
            "oa_url": oa["url"],
            "oa_resolver": oa["resolver"],
            "backfilled": True,
            "text_hash": corpus.text_hash(text),
            "text_length": len(corpus._normalize_text(text)),
            "artifact_path": artifact_rel,
        })
        blocks = corpus.build_blocks(
            {"raw_text": text, "document_type": doc.get("document_type", "html"), "artifact_path": artifact_rel},
            repo_root,
        )
        blocks["document_id"] = doc_id
        (blocks_dir / "{0}.json".format(doc_id)).write_text(json.dumps(blocks, indent=2) + "\n", encoding="utf-8")
        (blobs_dir / "{0}.txt".format(doc_id)).write_text(text, encoding="utf-8")
        _save(path, doc)
        report["fetched_full_text"] += 1
        report["details"].append({"document_id": doc_id, "doi": doi, "outcome": "full_text",
                                  "resolver": oa["resolver"], "chars": len(text)})

    (out_dir / "backfill_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {k: v for k, v in report.items() if k != "details"}


def _fetch_full_text(tools: CommandToolRunner, run_id: str, crop: str, url: str, doc: Dict[str, Any]):
    try:
        fetched = tools.invoke("fetch-web", {
            "run_id": "{0}-backfill".format(run_id), "source_index": 1,
            "source_url": url, "query": crop, "parameter_id": "",
        })
        parsed = tools.invoke("parse-document", {
            "query": crop, "crop": crop, "region_scope": {"level": "global", "name": "global"},
            "parameter_id": "", "parameter_family": "", "source_tier_id": doc.get("source_tier_id", ""),
            "source_tier_label": "", "document": fetched,
        })
        return parsed.get("raw_text", ""), fetched.get("artifact_path", "")
    except Exception:
        return "", ""


def _save(path: Path, doc: Dict[str, Any]) -> None:
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
