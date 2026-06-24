from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from .hooks import HookRunner
from .parameters import query_plan_for_run
from .schema_registry import SchemaRegistry
from .tool_runner import CommandToolRunner


class ExplorationRunner:
    def __init__(
        self,
        repo_root: Path,
        run_config_path: Path,
        manifest_path: Path,
        hook_config_path: Path,
    ) -> None:
        self.repo_root = repo_root
        self.registry = SchemaRegistry(repo_root)
        self.run_config_path = run_config_path
        self.manifest_path = manifest_path
        self.hooks = HookRunner(repo_root, hook_config_path)
        self.tools = CommandToolRunner(repo_root, manifest_path)
        self.run_config = self._load_run_config()

    def _load_run_config(self) -> Dict[str, Any]:
        with self.run_config_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.registry.validate("exploration-run.schema.json", payload)
        # Expand a curated seed registry into the inline source_seeds shape the
        # runner uses (WS-6). Inline source_seeds, if present, take precedence.
        if not payload.get("source_seeds") and payload.get("seed_registry_path"):
            from .seeds import seeds_for_run

            payload["source_seeds"] = seeds_for_run(self.repo_root, payload)
        return payload

    def execute(self) -> Dict[str, Any]:
        run_id = self.run_config["run_id"]
        run_dir = self.repo_root / "exploration" / "raw" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self._clear_previous_run_artifacts(run_dir)

        captures: List[Dict[str, Any]] = []
        query_summaries: List[Dict[str, Any]] = []
        seen_source_parameters = set()
        source_failure_count = 0
        search_failure_count = 0
        query_plan = query_plan_for_run(self.repo_root, self.run_config)
        source_tier_metrics = initial_source_tier_metrics(query_plan)

        for query_item in query_plan:
            query = query_item.query
            pre_search_event = self._hook_event(
                event_name="pre-search",
                run_id=run_id,
                payload={
                    "query": query,
                    "crop": self.run_config["crop"],
                    "region_scope": self.run_config["region_scope"]["name"],
                    "parameter_id": query_item.parameter_id,
                    "source_tier_id": query_item.source_tier_id,
                },
            )
            self.hooks.run_event(pre_search_event)

            search_error = ""
            try:
                search_response = self.tools.invoke(
                    self.run_config["tool_bindings"]["search"],
                    {
                        "query": query,
                        "crop": self.run_config["crop"],
                        "region_scope": self.run_config["region_scope"],
                        "parameter_id": query_item.parameter_id,
                        "parameter_family": query_item.parameter_family,
                        "source_tier_id": query_item.source_tier_id,
                        "source_tier_label": query_item.source_tier_label,
                        "max_results": self.run_config["max_results_per_query"],
                    },
                )
            except Exception as exc:
                search_failure_count += 1
                increment_tier_metric(source_tier_metrics, query_item.source_tier_id, "search_failures")
                search_error = str(exc)
                search_response = {"results": []}

            search_results = search_response.get("results", [])
            seed_results = []
            seed_mode = self.run_config.get("seed_mode", "fallback")
            if self.run_config.get("use_source_seeds", True) and (
                seed_mode == "augment" or not search_results
            ):
                seed_results = self._seed_results_for_query(query_item)
            results = self._merge_results(search_results, seed_results)
            increment_tier_metric(source_tier_metrics, query_item.source_tier_id, "search_hits", len(search_results))
            increment_tier_metric(source_tier_metrics, query_item.source_tier_id, "seed_hits", len(seed_results))
            increment_tier_metric(source_tier_metrics, query_item.source_tier_id, "results_returned", len(results))
            query_summaries.append(
                {
                    "query": query,
                    "parameter_id": query_item.parameter_id,
                    "parameter_family": query_item.parameter_family,
                    "parameter_label": query_item.parameter_label,
                    "source_tier_id": query_item.source_tier_id,
                    "source_tier_label": query_item.source_tier_label,
                    "results_returned": len(results),
                    "search_results_returned": len(search_results),
                    "seed_results_used": len(seed_results),
                    "search_error": search_error,
                    "provider_errors": search_response.get("provider_errors", []),
                    "discovery_method_counts": sorted_counts(
                        result.get("discovery_method", "unknown") for result in results
                    ),
                    "access_status_counts": sorted_counts(
                        result.get("access_status", "unknown") for result in results
                    ),
                    "tool": self.run_config["tool_bindings"]["search"],
                }
            )

            for search_result in results:
                source_url = search_result["source_url"]
                seen_key = (source_url, query_item.parameter_id or "__unparameterized__")
                if seen_key in seen_source_parameters:
                    continue
                seen_source_parameters.add(seen_key)

                try:
                    if search_result.get("access_status") == "metadata_only":
                        capture = self._metadata_only_capture(run_id, query_item, query, search_result, len(captures) + 1)
                        post_extract_event = self._hook_event(
                            event_name="post-extract",
                            run_id=run_id,
                            payload=capture,
                        )
                        self.hooks.run_event(post_extract_event)
                        captures.append(capture)
                        increment_tier_metric(source_tier_metrics, query_item.source_tier_id, "captured_sources")
                        increment_tier_metric(source_tier_metrics, query_item.source_tier_id, "metadata_only_captures")
                        self._write_capture(run_dir, capture)
                        continue

                    fetched = self.tools.invoke(
                        self.run_config["tool_bindings"]["fetch"],
                        {
                            "run_id": run_id,
                            "source_index": len(captures) + source_failure_count + 1,
                            "source_url": source_url,
                            "query": query,
                            "parameter_id": query_item.parameter_id,
                        },
                    )
                    increment_tier_metric(source_tier_metrics, query_item.source_tier_id, "fetch_successes")

                    post_fetch_event = self._hook_event(
                        event_name="post-fetch",
                        run_id=run_id,
                        payload={
                            "query": query,
                            "parameter_id": query_item.parameter_id,
                            "source_tier_id": query_item.source_tier_id,
                            "source_url": source_url,
                            "document_type": fetched["document_type"],
                            "fetch_status": fetched["fetch_status"],
                            "final_url": fetched.get("final_url", source_url),
                            "artifact_path": fetched.get("artifact_path", ""),
                        },
                    )
                    self.hooks.run_event(post_fetch_event)

                    parsed = self.tools.invoke(
                        self.run_config["tool_bindings"]["parse"],
                        {
                            "query": query,
                            "crop": self.run_config["crop"],
                            "region_scope": self.run_config["region_scope"],
                            "parameter_id": query_item.parameter_id,
                            "parameter_family": query_item.parameter_family,
                            "source_tier_id": query_item.source_tier_id,
                            "source_tier_label": query_item.source_tier_label,
                            "document": fetched,
                        },
                    )

                    capture = {
                        "id": self._capture_id(run_id, len(captures) + 1),
                        "run_id": run_id,
                        "query": query,
                        "parameter_id": query_item.parameter_id,
                        "parameter_family": query_item.parameter_family,
                        "parameter_label": query_item.parameter_label,
                        "source_tier_id": query_item.source_tier_id,
                        "source_tier_label": query_item.source_tier_label,
                        "discovery_method": search_result.get("discovery_method", "unknown"),
                        "access_status": fetched.get(
                            "access_status",
                            search_result.get("access_status", "open_full_text"),
                        ),
                        "source_metadata": search_result.get("source_metadata", {}),
                        "source_url": source_url,
                        "final_url": fetched.get("final_url", source_url),
                        "source_title": parsed.get("title_hint")
                        or fetched.get("title_hint")
                        or search_result.get("title", ""),
                        "source_domain": search_result.get("source_domain", ""),
                        "accessed_at": self._now_iso(),
                        "content_type": fetched.get("content_type", ""),
                        "document_type": fetched["document_type"],
                        "artifact_path": fetched.get("artifact_path", ""),
                        "search_title": search_result.get("title", ""),
                        "search_snippet": search_result.get("search_snippet", ""),
                        "snippet": parsed["snippet"],
                        "raw_text": parsed["raw_text"],
                        "publication_date_hint": parsed.get("publication_date_hint", ""),
                        "evidence_fragments": parsed.get("evidence_fragments", []),
                        "evidence_fragment_labels": parsed.get("evidence_fragment_labels", []),
                        "parser_used": parsed["parser_used"],
                        "candidate_claims": parsed["candidate_claims"],
                        "failures": parsed.get("failures", []),
                        "status": parsed.get("status", "parsed"),
                    }

                    post_extract_event = self._hook_event(
                        event_name="post-extract",
                        run_id=run_id,
                        payload=capture,
                    )
                    self.hooks.run_event(post_extract_event)
                    captures.append(capture)
                    increment_tier_metric(source_tier_metrics, query_item.source_tier_id, "captured_sources")
                    increment_tier_metric(source_tier_metrics, query_item.source_tier_id, "parse_successes")
                    increment_tier_metric(
                        source_tier_metrics,
                        query_item.source_tier_id,
                        "candidate_claims",
                        len(capture["candidate_claims"]),
                    )

                    self._write_capture(run_dir, capture)
                except Exception as exc:
                    source_failure_count += 1
                    increment_tier_metric(source_tier_metrics, query_item.source_tier_id, "source_failures")
                    failure_event = self._hook_event(
                        event_name="on-failure",
                        run_id=run_id,
                        payload={
                            "query": query,
                            "parameter_id": query_item.parameter_id,
                            "source_tier_id": query_item.source_tier_id,
                            "source_url": source_url,
                            "message": str(exc),
                        },
                    )
                    self.hooks.run_event(failure_event)

        summary = {
            "run_id": run_id,
            "crop": self.run_config["crop"],
            "region_scope": self.run_config["region_scope"],
            "parameter_manifest_path": self.run_config.get("parameter_manifest_path", ""),
            "crop_profile_path": self.run_config.get("crop_profile_path", ""),
            "source_tier_policy_path": self.run_config.get("source_tier_policy_path", ""),
            "source_tier_policy_id": self.run_config.get("source_tier_policy_id", ""),
            "source_tier_ids": self.run_config.get("source_tier_ids", []),
            "parameter_families": self.run_config.get("parameter_families", []),
            "parameter_ids": self.run_config.get("parameter_ids", []),
            "queries_executed": len(query_plan),
            "unique_sources_captured": len(captures),
            "candidate_claim_count": sum(len(capture["candidate_claims"]) for capture in captures),
            "failure_count": search_failure_count + source_failure_count,
            "search_failure_count": search_failure_count,
            "source_failure_count": source_failure_count,
            "source_tier_metrics": source_tier_metric_rows(source_tier_metrics),
            "access_status_counts": sorted_counts(capture.get("access_status", "unknown") for capture in captures),
            "discovery_method_counts": sorted_counts(
                capture.get("discovery_method", "unknown") for capture in captures
            ),
            "query_summaries": query_summaries,
            "artifacts_dir": str(run_dir.relative_to(self.repo_root)),
            "generated_at": self._now_iso(),
        }
        summary_path = run_dir / "summary.json"
        with summary_path.open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
            handle.write("\n")
        return summary

    def _clear_previous_run_artifacts(self, run_dir: Path) -> None:
        for capture_path in run_dir.glob("*-capture-*.json"):
            capture_path.unlink()
        summary_path = run_dir / "summary.json"
        if summary_path.exists():
            summary_path.unlink()
        fetched_dir = run_dir / "fetched"
        if fetched_dir.exists():
            shutil.rmtree(fetched_dir)

    def _metadata_only_capture(
        self,
        run_id: str,
        query_item: Any,
        query: str,
        search_result: Dict[str, Any],
        index: int,
    ) -> Dict[str, Any]:
        source_url = search_result["source_url"]
        source_metadata = search_result.get("source_metadata", {})
        publication_hint = str(source_metadata.get("publication_year", ""))
        return {
            "id": self._capture_id(run_id, index),
            "run_id": run_id,
            "query": query,
            "parameter_id": query_item.parameter_id,
            "parameter_family": query_item.parameter_family,
            "parameter_label": query_item.parameter_label,
            "source_tier_id": query_item.source_tier_id,
            "source_tier_label": query_item.source_tier_label,
            "discovery_method": search_result.get("discovery_method", "unknown"),
            "access_status": "metadata_only",
            "source_metadata": source_metadata,
            "source_url": source_url,
            "final_url": source_url,
            "source_title": search_result.get("title", source_url),
            "source_domain": search_result.get("source_domain", urlparse(source_url).netloc.lower()),
            "accessed_at": self._now_iso(),
            "content_type": "",
            "document_type": search_result.get("document_type", "other"),
            "artifact_path": "",
            "search_title": search_result.get("title", ""),
            "search_snippet": search_result.get("search_snippet", ""),
            "snippet": search_result.get("search_snippet", ""),
            "raw_text": "",
            "publication_date_hint": publication_hint,
            "evidence_fragments": [],
            "evidence_fragment_labels": [],
            "parser_used": "metadata-only",
            "candidate_claims": [],
            "failures": [],
            "status": "captured",
        }

    def _write_capture(self, run_dir: Path, capture: Dict[str, Any]) -> None:
        capture_path = run_dir / "{0}.json".format(capture["id"])
        with capture_path.open("w", encoding="utf-8") as handle:
            json.dump(capture, handle, indent=2)
            handle.write("\n")

    @staticmethod
    def _merge_results(
        search_results: List[Dict[str, Any]], seed_results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Combine live search and seed results, de-duplicating by source_url.

        Live search results take precedence; a seed whose URL already appeared
        in search is dropped so augment-mode seeds never double-count a source.
        """
        merged = list(search_results)
        seen = {result.get("source_url") for result in search_results}
        for seed in seed_results:
            if seed.get("source_url") in seen:
                continue
            seen.add(seed.get("source_url"))
            merged.append(seed)
        return merged

    def _seed_results_for_query(self, query_item: Any) -> List[Dict[str, Any]]:
        ranked_results: List[Tuple[int, Dict[str, Any]]] = []
        max_results = int(self.run_config.get("max_results_per_query", 3))
        for seed in self.run_config.get("source_seeds", []):
            if not self._seed_matches_query(seed, query_item):
                continue
            source_url = seed["source_url"]
            parsed = urlparse(source_url)
            ranked_results.append(
                (
                    self._seed_rank(seed, query_item),
                    {
                        "title": seed.get("title", source_url),
                        "source_url": source_url,
                        "document_type": seed.get(
                            "document_type",
                            "pdf" if source_url.lower().endswith(".pdf") else "html",
                        ),
                        "search_snippet": seed.get("snippet", "Trusted source seed used when live search returned no results."),
                        "source_domain": parsed.netloc.lower(),
                        "score": 0,
                        "discovery_method": "source_seed",
                        "source_tier_id": seed.get("source_tier_id", ""),
                    },
                )
            )
        ranked_results.sort(key=lambda item: item[0])
        deduped_results: List[Dict[str, Any]] = []
        seen_urls = set()
        for _, result in ranked_results:
            if result["source_url"] in seen_urls:
                continue
            seen_urls.add(result["source_url"])
            deduped_results.append(result)
            if len(deduped_results) >= max_results:
                break
        return deduped_results

    def _seed_rank(self, seed: Dict[str, Any], query_item: Any) -> int:
        if query_item.parameter_id in set(seed.get("parameter_ids", [])):
            return 0
        if query_item.parameter_family in set(seed.get("parameter_families", [])):
            return 1
        if query_item.source_tier_id and query_item.source_tier_id == seed.get("source_tier_id", ""):
            return 2
        return 2

    def _seed_matches_query(self, seed: Dict[str, Any], query_item: Any) -> bool:
        parameter_ids = set(seed.get("parameter_ids", []))
        parameter_families = set(seed.get("parameter_families", []))
        source_tier_id = seed.get("source_tier_id", "")
        if source_tier_id and query_item.source_tier_id and source_tier_id != query_item.source_tier_id:
            return False
        if parameter_ids and query_item.parameter_id not in parameter_ids:
            return False
        if parameter_families and query_item.parameter_family not in parameter_families:
            return False
        return True

    def _hook_event(self, event_name: str, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "event_name": event_name,
            "run_id": run_id,
            "occurred_at": self._now_iso(),
            "payload": payload,
            "status": "received",
        }

    def _capture_id(self, run_id: str, index: int) -> str:
        sanitized = re.sub(r"[^a-z0-9]+", "-", run_id.lower()).strip("-")
        return "{0}-capture-{1:03d}".format(sanitized, index)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


SOURCE_TIER_METRIC_FIELDS = (
    "planned_queries",
    "search_hits",
    "seed_hits",
    "results_returned",
    "fetch_successes",
    "parse_successes",
    "metadata_only_captures",
    "captured_sources",
    "candidate_claims",
    "search_failures",
    "source_failures",
)


def initial_source_tier_metrics(query_plan: List[Any]) -> Dict[str, Dict[str, Any]]:
    metrics: Dict[str, Dict[str, Any]] = {}
    for query_item in query_plan:
        record = ensure_source_tier_metric(metrics, query_item.source_tier_id, query_item.source_tier_label)
        record["planned_queries"] += 1
    return metrics


def ensure_source_tier_metric(
    metrics: Dict[str, Dict[str, Any]],
    source_tier_id: str,
    source_tier_label: str = "",
) -> Dict[str, Any]:
    key = source_tier_key(source_tier_id)
    if key not in metrics:
        metrics[key] = {
            "source_tier_id": source_tier_id,
            "source_tier_label": source_tier_label or ("Unspecified" if not source_tier_id else source_tier_id),
            **{field: 0 for field in SOURCE_TIER_METRIC_FIELDS},
        }
    elif source_tier_label and not metrics[key].get("source_tier_label"):
        metrics[key]["source_tier_label"] = source_tier_label
    return metrics[key]


def increment_tier_metric(
    metrics: Dict[str, Dict[str, Any]],
    source_tier_id: str,
    field: str,
    amount: int = 1,
) -> None:
    ensure_source_tier_metric(metrics, source_tier_id)[field] += amount


def source_tier_metric_rows(metrics: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [metrics[key] for key in sorted(metrics)]


def source_tier_key(source_tier_id: str) -> str:
    return source_tier_id or "__unspecified__"


def sorted_counts(values: Any) -> Dict[str, int]:
    counts = Counter(value for value in values if value)
    return {key: counts[key] for key in sorted(counts)}
