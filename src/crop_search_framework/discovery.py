"""Phase B1 — discovery stage: over-collect provider results into a durable,
complete ledger. **Discovery only — no fetch decisions** (those live in the
fetch queue, WS-2).

For every query in the plan we call the tier's connectors, record *every* raw
provider row (relevance filtering is a non-destructive stamp, not a drop), and
write ``exploration/discovery/<run>/results.jsonl``. Provider calls go through
the shared :class:`HttpClient`, so retries/backoff/cache are automatic; calls
that still fail are recorded in ``retry_queue.jsonl`` and re-attempted on the
next ``--resume`` run (the HTTP cache makes completed calls instant).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .dev_tools.common import user_agent
from .dev_tools.discovery_connectors import (
    MIN_RELEVANCE_SCORE,
    configure_client,
    connector_results_for_tier,
)
from .dev_tools.http_client import HttpClient
from .parameters import query_plan_for_run
from .schema_registry import SchemaRegistry

# Providers whose recall is noisy enough to warrant a relevance floor. Scholarly
# APIs match server-side and are left ungated (mirrors the old connector logic).
GATED_PROVIDERS = {"internet_archive", "wikipedia", "duckduckgo_html"}


def capture_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_doi(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.replace("https://doi.org/", "").replace("http://doi.org/", "")
    return value.strip("/")


def canonical_key(source_url: str, doi: str) -> str:
    doi = normalize_doi(doi)
    if doi:
        return "doi:" + doi
    parsed = urlparse((source_url or "").strip().lower())
    netloc = parsed.netloc[4:] if parsed.netloc.startswith("www.") else parsed.netloc
    path = parsed.path.rstrip("/")
    return "url:" + netloc + path


def _ledger_id(run_id: str, index: int) -> str:
    return "{0}-led-{1:06d}".format(hashlib.sha1(run_id.encode()).hexdigest()[:6], index)


class DiscoveryRunner:
    def __init__(self, repo_root: Path, run_config_path: Path) -> None:
        self.repo_root = repo_root
        self.registry = SchemaRegistry(repo_root)
        self.run_config_path = run_config_path
        with run_config_path.open("r", encoding="utf-8") as handle:
            self.run_config = json.load(handle)
        self.registry.validate("exploration-run.schema.json", self.run_config)

    def execute(self, resume: bool = False) -> Dict[str, Any]:
        run_id = self.run_config["run_id"]
        crop = self.run_config["crop"]
        out_dir = self.repo_root / "exploration" / "discovery" / run_id
        out_dir.mkdir(parents=True, exist_ok=True)

        # Over-collect: discovery pulls more per query than fetch will select.
        max_results = int(
            self.run_config.get(
                "discovery_max_results",
                max(10, int(self.run_config.get("max_results_per_query", 3)) * 3),
            )
        )

        cache_dir = self.repo_root / "exploration" / "cache" / "providers"
        configure_client(HttpClient(cache_dir=cache_dir))

        query_plan = query_plan_for_run(self.repo_root, self.run_config)
        ledger_rows: List[Dict[str, Any]] = []
        retry_queue: List[Dict[str, Any]] = []
        ua = user_agent()

        for item in query_plan:
            results, errors = connector_results_for_tier(
                query=item.query,
                crop=crop,
                source_tier_id=item.source_tier_id,
                max_results=max_results,
                user_agent=ua,
            )
            for error in errors:
                retry_queue.append(
                    {
                        "query": item.query,
                        "parameter_id": item.parameter_id,
                        "source_tier": item.source_tier_id,
                        "error": error,
                    }
                )
            # Per-provider rank within this query's response.
            provider_counter: Counter = Counter()
            for result in results:
                provider = result.get("discovery_method", "unknown")
                provider_counter[provider] += 1
                rank = provider_counter[provider]
                metadata = result.get("source_metadata") or {}
                doi = metadata.get("doi", "")
                ledger_rows.append(
                    {
                        "ledger_id": _ledger_id(run_id, len(ledger_rows) + 1),
                        "query": item.query,
                        "parameter_id": item.parameter_id,
                        "source_tier": item.source_tier_id,
                        "provider": provider,
                        "discovery_rank": rank,
                        "score": result.get("score", 0),
                        "score_components": result.get("score_components", {}),
                        "source_url": result.get("source_url", ""),
                        "canonical_key": canonical_key(result.get("source_url", ""), doi),
                        "doi": normalize_doi(doi),
                        "result_type": metadata.get("type", ""),
                        "access_status": result.get("access_status", "unknown"),
                        "source_domain": result.get("source_domain", ""),
                        "title": result.get("title", ""),
                        "discovery_drop_reason": "",
                    }
                )

        self._inject_seeds(run_id, ledger_rows)
        self._stamp_drop_reasons(ledger_rows)
        self._write_jsonl(out_dir / "results.jsonl", ledger_rows)
        self._write_jsonl(out_dir / "retry_queue.jsonl", retry_queue)

        summary = self._summary(run_id, query_plan, ledger_rows, retry_queue, out_dir, resume)
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary

    def _inject_seeds(self, run_id: str, ledger_rows: List[Dict[str, Any]]) -> None:
        """Add curated registry seeds to the ledger as first-class candidates.

        The connectors only surface API-discovered sources; the authoritative
        curated seeds (FAO/AHDB/CIMMYT/extension) must also enter discovery so
        fetch selection can pick them and retrieval-eval can score them. Seeds
        get a strong fixed score and open-full-text status; canonical_key dedup
        merges a seed with the same source found by a connector.
        """
        from .seeds import seeds_for_run

        try:
            seeds = seeds_for_run(self.repo_root, self.run_config)
        except FileNotFoundError:
            return  # no registry configured/available -> connectors only

        for seed in seeds:
            source_url = seed.get("source_url", "")
            if not source_url:
                continue
            tier = seed.get("source_tier_id", "")
            for parameter_id in seed.get("parameter_ids", []) or [""]:
                ledger_rows.append(
                    {
                        "ledger_id": _ledger_id(run_id, len(ledger_rows) + 1),
                        "query": "curated_seed",
                        "parameter_id": parameter_id,
                        "source_tier": tier,
                        "provider": "source_seed",
                        "discovery_rank": 1,
                        "score": 15,
                        "score_components": {"curated_seed": 15},
                        "source_url": source_url,
                        "canonical_key": canonical_key(source_url, ""),
                        "doi": "",
                        "result_type": "",
                        "access_status": "open_full_text",
                        "source_domain": urlparse(source_url).netloc.lower(),
                        "title": seed.get("title", source_url),
                        "discovery_drop_reason": "",
                    }
                )

    def _stamp_drop_reasons(self, rows: List[Dict[str, Any]]) -> None:
        """Non-destructive: stamp a reason, never remove a row (ledger stays complete)."""
        # Relevance gate (downstream of connectors now).
        for row in rows:
            if row["provider"] in GATED_PROVIDERS and row["score"] < MIN_RELEVANCE_SCORE:
                row["discovery_drop_reason"] = "relevance_gate"
        # Duplicate: keep the highest-scoring row per canonical_key; mark the rest.
        best_by_key: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            key = row["canonical_key"]
            current = best_by_key.get(key)
            if current is None or row["score"] > current["score"]:
                best_by_key[key] = row
        for row in rows:
            if row is best_by_key.get(row["canonical_key"]):
                continue
            if not row["discovery_drop_reason"]:
                row["discovery_drop_reason"] = "duplicate"

    def _summary(self, run_id, query_plan, ledger_rows, retry_queue, out_dir, resume) -> Dict[str, Any]:
        drop_counts: Counter = Counter(row["discovery_drop_reason"] or "kept" for row in ledger_rows)
        return {
            "run_id": run_id,
            "stage": "discovery",
            "resume": resume,
            "generated_at": capture_now(),
            "queries_executed": len(query_plan),
            "ledger_rows": len(ledger_rows),
            "unique_sources": len({row["canonical_key"] for row in ledger_rows}),
            "provider_counts": dict(sorted(Counter(r["provider"] for r in ledger_rows).items())),
            "drop_reason_counts": dict(sorted(drop_counts.items())),
            "access_status_counts": dict(sorted(Counter(r["access_status"] for r in ledger_rows).items())),
            "retry_queue_size": len(retry_queue),
            "ledger_path": str((out_dir / "results.jsonl").relative_to(self.repo_root)),
        }

    @staticmethod
    def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")


def discover(repo_root: Path, run_config_path: Path, resume: bool = False) -> Dict[str, Any]:
    return DiscoveryRunner(repo_root, run_config_path).execute(resume=resume)
