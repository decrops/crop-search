"""Phase B2 (final wiring) — fetch executor: turn the fetch queue into raw captures.

This is the bridge that was missing between ``select-fetch`` and ``build-corpus``.
It consumes ``exploration/discovery/<run>/fetch_queue.jsonl`` (the selected,
balanced, OA-resolved candidates) and the discovery ledger, fetches each selected
URL through the shared :class:`HttpClient` (binary mode → cache + backoff +
resume; uses ``resolved_oa_url`` when present), parses it with the existing
``parse_document`` functions, and writes raw captures + a summary that
``build-corpus`` already understands.

One capture is emitted per (document, parameter_id) pointed at by the queue row,
recovering the query text from the ledger so the corpus ``query_hits`` keep the
full many-to-many association. Paywalled / metadata-only rows become
metadata-only captures (no fetch). With ``resume=True`` the HTTP cache makes
completed fetches instant and existing captures are not rewritten.

This makes the reworked path end-to-end: discover → select-fetch → **fetch** →
build-corpus → corpus-qa → (Opus) → normalize → review → promote → render-vault.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .dev_tools.common import user_agent
from .dev_tools.fetch_web import infer_document_type, pick_suffix, safe_name, extract_title_hint
from .dev_tools.http_client import HttpClient, HttpError
from .dev_tools.parse_document import (
    evidence_fragment_labels,
    parse_html,
    parse_pdf,
    select_claims,
    sentence_split,
)
from .parameters import load_parameter_manifest, parameter_by_id
from .schema_registry import SchemaRegistry


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _capture_id(run_id: str, index: int) -> str:
    sanitized = re.sub(r"[^a-z0-9]+", "-", run_id.lower()).strip("-")
    return "{0}-capture-{1:03d}".format(sanitized, index)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class FetchRunner:
    def __init__(self, repo_root: Path, run_config_path: Path) -> None:
        self.repo_root = repo_root
        self.registry = SchemaRegistry(repo_root)
        with run_config_path.open("r", encoding="utf-8") as handle:
            self.run_config = json.load(handle)
        self.registry.validate("exploration-run.schema.json", self.run_config)
        self.param_labels = self._load_param_labels()

    def _load_param_labels(self) -> Dict[str, str]:
        path = self.run_config.get("parameter_manifest_path")
        if not path:
            return {}
        manifest = load_parameter_manifest(self.repo_root, path)
        return {pid: p.get("label", "") for pid, p in parameter_by_id(manifest).items()}

    def execute(self, resume: bool = False, limit: Optional[int] = None) -> Dict[str, Any]:
        run_id = self.run_config["run_id"]
        crop = self.run_config["crop"]
        discovery_dir = self.repo_root / "exploration" / "discovery" / run_id
        queue = [r for r in _read_jsonl(discovery_dir / "fetch_queue.jsonl") if r.get("fetch_selected")]
        ledger_by_id = {r["ledger_id"]: r for r in _read_jsonl(discovery_dir / "results.jsonl")}
        if limit:
            queue = queue[:limit]

        raw_dir = self.repo_root / "exploration" / "raw" / run_id
        fetched_dir = raw_dir / "fetched"
        if not resume:
            self._clear(raw_dir)
        fetched_dir.mkdir(parents=True, exist_ok=True)

        client = HttpClient(cache_dir=self.repo_root / "exploration" / "cache" / "fetch")
        ua = user_agent()

        captures: List[Dict[str, Any]] = []
        fetch_ok = fetch_failed = metadata_only = 0
        index = 0

        for row in queue:
            params = self._params_for_row(row, ledger_by_id)
            fetch_url = row.get("resolved_oa_url") or row.get("source_url", "")
            is_metadata_only = row.get("access_status") == "metadata_only" and not row.get("resolved_oa_url")

            parsed: Optional[Dict[str, Any]] = None
            fetched_meta: Optional[Dict[str, Any]] = None
            if not is_metadata_only:
                fetched_meta, parsed, error = self._fetch_and_parse(client, fetch_url, index + 1, crop, params, ua)
                if parsed is None:
                    fetch_failed += 1
                    is_metadata_only = True  # degrade to metadata-only on fetch/parse failure
                else:
                    fetch_ok += 1

            for pid, query in params.items():
                index += 1
                capture = self._build_capture(
                    run_id, index, row, pid, query, parsed, fetched_meta, is_metadata_only
                )
                self.registry.validate("raw-capture.schema.json", capture)
                self._write_capture(raw_dir, capture, resume)
                captures.append(capture)
            if is_metadata_only and parsed is None:
                metadata_only += 1

        summary = {
            "run_id": run_id,
            "crop": crop,
            "stage": "fetch",
            "generated_at": _now_iso(),
            "parameter_manifest_path": self.run_config.get("parameter_manifest_path", ""),
            "crop_profile_path": self.run_config.get("crop_profile_path", ""),
            "selected_queue_rows": len(queue),
            "unique_sources_captured": len({c["source_url"] for c in captures}),
            "captures_written": len(captures),
            "fetch_successes": fetch_ok,
            "fetch_failures": fetch_failed,
            "metadata_only_sources": metadata_only,
            "candidate_claim_count": sum(len(c.get("candidate_claims", [])) for c in captures),
            "failure_count": fetch_failed,
        }
        (raw_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary

    def _params_for_row(self, row: Dict[str, Any], ledger_by_id: Dict[str, Any]) -> Dict[str, str]:
        """Return {parameter_id: query} from the ledger rows that point here."""
        param_to_query: Dict[str, str] = {}
        for lid in row.get("ledger_ids", []):
            lr = ledger_by_id.get(lid)
            if lr and lr.get("parameter_id"):
                param_to_query.setdefault(lr["parameter_id"], lr.get("query", ""))
        if not param_to_query:
            for pid in row.get("parameter_ids", []):
                param_to_query.setdefault(pid, "")
        return param_to_query or {"": ""}

    def _fetch_and_parse(self, client, url, source_index, crop, params, ua):
        if not url:
            return None, None, "no url"
        try:
            response = client.get_binary(url, headers={"User-Agent": ua}, timeout=35)
        except HttpError as exc:
            return None, None, str(exc)
        final_url = response.url or url
        content_type = response.headers.get("content-type") or response.headers.get("Content-Type") or ""
        document_type = infer_document_type(final_url, content_type)
        domain = urlparse(final_url).netloc.lower()
        suffix = pick_suffix(final_url, document_type)
        artifact_path = (
            self.repo_root / "exploration" / "raw" / self.run_config["run_id"] / "fetched"
            / "{0:03d}-{1}{2}".format(source_index, safe_name(domain or "source"), suffix)
        )
        artifact_path.write_bytes(response.content)
        title_hint = extract_title_hint(response.text) if document_type == "html" else ""
        fetched_meta = {
            "final_url": final_url,
            "content_type": content_type,
            "document_type": document_type,
            "artifact_path": str(artifact_path.relative_to(self.repo_root)),
            "title_hint": title_hint,
        }
        try:
            parsed = parse_pdf(artifact_path) if document_type == "pdf" else parse_html(artifact_path)
        except Exception as exc:  # pragma: no cover - parser/runtime failure
            return fetched_meta, None, str(exc)
        # Attach candidate claims using the first associated query for relevance.
        first_query = next(iter(params.values()), "")
        sentences = sentence_split(parsed["raw_text"])
        parsed["candidate_claims"] = select_claims(
            sentences=sentences,
            source_title=parsed.get("title_hint") or title_hint,
            query=first_query,
            crop=crop,
        )
        if not parsed["raw_text"]:
            return fetched_meta, None, "empty text"
        return fetched_meta, parsed, ""

    def _build_capture(self, run_id, index, row, pid, query, parsed, fetched_meta, is_metadata_only):
        source_url = row.get("source_url", "")
        domain = row.get("source_domain") or urlparse(source_url).netloc.lower()
        family = pid.split(".", 1)[0] if pid else ""
        base = {
            "id": _capture_id(run_id, index),
            "run_id": run_id,
            "query": query,
            "parameter_id": pid,
            "parameter_family": family,
            "parameter_label": self.param_labels.get(pid, ""),
            "source_tier_id": row.get("source_tier", ""),
            "source_tier_label": "",
            "discovery_method": "discover_pipeline",
            "source_metadata": {"doi": row.get("doi", ""), "canonical_key": row.get("canonical_key", "")},
            "source_url": source_url,
            "source_domain": domain,
            "accessed_at": _now_iso(),
            "search_title": row.get("title", ""),
            "search_snippet": "",
        }
        if is_metadata_only or parsed is None:
            base.update({
                "access_status": "metadata_only",
                "final_url": source_url,
                "source_title": row.get("title", source_url),
                "content_type": "",
                "document_type": "other",
                "artifact_path": "",
                "snippet": "",
                "raw_text": "",
                "publication_date_hint": "",
                "evidence_fragments": [],
                "evidence_fragment_labels": [],
                "parser_used": "metadata-only",
                "candidate_claims": [],
                "failures": [],
                "status": "captured",
            })
            return base
        fragments = parsed["candidate_claims"][:5]
        base.update({
            "access_status": "open_full_text",
            "final_url": fetched_meta["final_url"],
            "source_title": parsed.get("title_hint") or fetched_meta.get("title_hint") or row.get("title", source_url),
            "content_type": fetched_meta["content_type"],
            "document_type": fetched_meta["document_type"],
            "artifact_path": fetched_meta["artifact_path"],
            "snippet": parsed["snippet"],
            "raw_text": parsed["raw_text"],
            "publication_date_hint": parsed.get("publication_date_hint", ""),
            "evidence_fragments": fragments,
            "evidence_fragment_labels": evidence_fragment_labels(fragments),
            "parser_used": parsed.get("parser_used", "parse-document"),
            "candidate_claims": parsed["candidate_claims"],
            "failures": parsed.get("failures", []),
            "status": parsed.get("status", "parsed"),
        })
        return base

    def _write_capture(self, raw_dir: Path, capture: Dict[str, Any], resume: bool) -> None:
        path = raw_dir / "{0}.json".format(capture["id"])
        if resume and path.exists():
            return
        with path.open("w", encoding="utf-8") as handle:
            json.dump(capture, handle, indent=2)
            handle.write("\n")

    def _clear(self, raw_dir: Path) -> None:
        if raw_dir.exists():
            for capture_path in raw_dir.glob("*.json"):
                capture_path.unlink()
            fetched = raw_dir / "fetched"
            if fetched.exists():
                shutil.rmtree(fetched)


def run_fetch(repo_root: Path, run_config_path: Path, resume: bool = False, limit: Optional[int] = None) -> Dict[str, Any]:
    return FetchRunner(repo_root, run_config_path).execute(resume=resume, limit=limit)
