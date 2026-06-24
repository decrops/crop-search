from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from .dev_tools.common import user_agent
from .dev_tools.discovery_connectors import (
    MIN_RELEVANCE_SCORE,
    configure_client,
    connector_results_for_tier,
)
from .dev_tools.http_client import HttpClient
from .discovery import canonical_key, normalize_doi
from .parameters import dedupe_words
from .schema_registry import SchemaRegistry
from .source_tiers import selected_source_tiers


DEFAULT_VOCABULARY_PATH = "config/relationships/relationship-vocabulary.json"
DEFAULT_CROP_DIR = "config/crops"
DEFAULT_SOURCE_TIER_POLICY_PATH = "config/source-tiers/default.json"
GATED_PROVIDERS = {"internet_archive", "wikipedia", "duckduckgo_html"}


@dataclass(frozen=True)
class CropNode:
    crop_id: str
    label: str
    crop_group: str
    aliases: Tuple[str, ...]
    scientific_names: Tuple[str, ...]

    @property
    def search_term(self) -> str:
        return self.aliases[0] if self.aliases else self.label

    def to_json(self) -> Dict[str, Any]:
        return {
            "crop_id": self.crop_id,
            "label": self.label,
            "crop_group": self.crop_group,
            "aliases": list(self.aliases),
            "scientific_names": list(self.scientific_names),
        }


@dataclass(frozen=True)
class RelationshipQueryPlanItem:
    query: str
    subject_crop_id: str
    object_crop_id: str
    subject_crop_label: str
    object_crop_label: str
    relationship_mode: str
    relationship_subtype: str
    directionality: str
    ordered_pair_key: str
    canonical_relationship_key: str
    source_tier_id: str
    source_tier_label: str

    def to_json(self) -> Dict[str, Any]:
        return {
            "query_kind": "crop_relationship",
            "query": self.query,
            "subject_crop_id": self.subject_crop_id,
            "object_crop_id": self.object_crop_id,
            "subject_crop_label": self.subject_crop_label,
            "object_crop_label": self.object_crop_label,
            "relationship_mode": self.relationship_mode,
            "relationship_subtype": self.relationship_subtype,
            "directionality": self.directionality,
            "ordered_pair_key": self.ordered_pair_key,
            "canonical_relationship_key": self.canonical_relationship_key,
            "source_tier_id": self.source_tier_id,
            "source_tier_label": self.source_tier_label,
        }


def resolve_repo_path(repo_root: Path, path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else repo_root / path


def current_time() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_relationship_vocabulary(
    repo_root: Path,
    vocabulary_path: str = DEFAULT_VOCABULARY_PATH,
) -> Dict[str, Any]:
    payload = load_json(resolve_repo_path(repo_root, vocabulary_path))
    SchemaRegistry(repo_root).validate("crop-relationship-vocabulary.schema.json", payload)
    mode_ids = [mode["mode_id"] for mode in payload["modes"]]
    duplicate_ids = sorted({mode_id for mode_id in mode_ids if mode_ids.count(mode_id) > 1})
    if duplicate_ids:
        raise ValueError("Duplicate relationship mode ids: {0}".format(", ".join(duplicate_ids)))
    missing_defaults = [mode_id for mode_id in payload.get("default_modes", []) if mode_id not in mode_ids]
    if missing_defaults:
        raise ValueError("Default relationship modes are not defined: {0}".format(", ".join(missing_defaults)))
    return payload


def load_crop_universe(
    repo_root: Path,
    crop_dir: str = DEFAULT_CROP_DIR,
) -> List[CropNode]:
    base = resolve_repo_path(repo_root, crop_dir)
    if not base.exists():
        raise FileNotFoundError("Crop directory not found: {0}".format(crop_dir))
    registry = SchemaRegistry(repo_root)
    crops: List[CropNode] = []
    for path in sorted(base.glob("*.json")):
        payload = load_json(path)
        registry.validate("crop-profile.schema.json", payload)
        crops.append(
            CropNode(
                crop_id=payload["crop_id"],
                label=payload["label"],
                crop_group=payload["crop_group"],
                aliases=tuple(payload.get("aliases", [])),
                scientific_names=tuple(payload.get("scientific_names", [])),
            )
        )
    if not crops:
        raise ValueError("No crop profiles found in {0}".format(crop_dir))
    crop_ids = [crop.crop_id for crop in crops]
    duplicate_ids = sorted({crop_id for crop_id in crop_ids if crop_ids.count(crop_id) > 1})
    if duplicate_ids:
        raise ValueError("Duplicate crop profile ids: {0}".format(", ".join(duplicate_ids)))
    return sorted(crops, key=lambda crop: crop.crop_id)


def modes_by_id(vocabulary: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {mode["mode_id"]: mode for mode in vocabulary["modes"]}


def selected_modes(
    vocabulary: Dict[str, Any],
    mode_ids: Optional[Sequence[str]] = None,
    *,
    all_when_unspecified: bool = False,
) -> List[Dict[str, Any]]:
    by_id = modes_by_id(vocabulary)
    if mode_ids:
        requested = list(mode_ids)
    elif all_when_unspecified:
        requested = [mode["mode_id"] for mode in vocabulary["modes"]]
    else:
        requested = list(vocabulary.get("default_modes", []))
    missing = [mode_id for mode_id in requested if mode_id not in by_id]
    if missing:
        raise ValueError("Unknown relationship mode ids: {0}".format(", ".join(missing)))
    return [by_id[mode_id] for mode_id in requested]


def ordered_pair_key(subject_crop_id: str, object_crop_id: str) -> str:
    return "{0}|{1}".format(subject_crop_id, object_crop_id)


def canonical_relationship_key(mode: Dict[str, Any], subject_crop_id: str, object_crop_id: str) -> str:
    if mode["directionality"] == "symmetric":
        left, right = sorted([subject_crop_id, object_crop_id])
        return "{0}|{1}|{2}".format(mode["mode_id"], left, right)
    return "{0}|{1}|{2}".format(mode["mode_id"], subject_crop_id, object_crop_id)


def ordered_crop_pairs(crops: Sequence[CropNode], include_self_pairs: bool = True) -> List[Tuple[CropNode, CropNode]]:
    pairs: List[Tuple[CropNode, CropNode]] = []
    for subject in crops:
        for obj in crops:
            if not include_self_pairs and subject.crop_id == obj.crop_id:
                continue
            pairs.append((subject, obj))
    return pairs


def build_relationship_matrix(
    repo_root: Path,
    *,
    crop_dir: str = DEFAULT_CROP_DIR,
    vocabulary_path: str = DEFAULT_VOCABULARY_PATH,
    mode_ids: Optional[Sequence[str]] = None,
    include_self_pairs: bool = True,
) -> Dict[str, Any]:
    crops = load_crop_universe(repo_root, crop_dir)
    vocabulary = load_relationship_vocabulary(repo_root, vocabulary_path)
    modes = selected_modes(vocabulary, mode_ids, all_when_unspecified=True)
    cells = []
    for subject, obj in ordered_crop_pairs(crops, include_self_pairs=include_self_pairs):
        mode_statuses = {}
        for mode in modes:
            mode_statuses[mode["mode_id"]] = {
                "status": "not_searched",
                "summary_effect": "unknown",
                "canonical_relationship_key": canonical_relationship_key(mode, subject.crop_id, obj.crop_id),
                "evidence_count": 0,
                "conflict_count": 0,
            }
        cells.append(
            {
                "subject_crop_id": subject.crop_id,
                "object_crop_id": obj.crop_id,
                "subject_crop_group": subject.crop_group,
                "object_crop_group": obj.crop_group,
                "ordered_pair_key": ordered_pair_key(subject.crop_id, obj.crop_id),
                "mode_statuses": mode_statuses,
            }
        )
    return {
        "version": "0.1.0",
        "generated_at": current_time(),
        "crop_universe_source": crop_dir,
        "relationship_vocabulary_version": vocabulary["version"],
        "relationship_modes": [mode["mode_id"] for mode in modes],
        "crop_count": len(crops),
        "cell_count": len(cells),
        "crops": [crop.to_json() for crop in crops],
        "cells": cells,
        "rollups": [],
    }


def write_relationship_matrix(
    repo_root: Path,
    output_path: Path,
    *,
    crop_dir: str = DEFAULT_CROP_DIR,
    vocabulary_path: str = DEFAULT_VOCABULARY_PATH,
    mode_ids: Optional[Sequence[str]] = None,
    include_self_pairs: bool = True,
) -> Dict[str, Any]:
    matrix = build_relationship_matrix(
        repo_root,
        crop_dir=crop_dir,
        vocabulary_path=vocabulary_path,
        mode_ids=mode_ids,
        include_self_pairs=include_self_pairs,
    )
    SchemaRegistry(repo_root).validate("crop-relationship-matrix.schema.json", matrix)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(matrix, handle, indent=2)
        handle.write("\n")
    return {
        "matrix_path": render_path(repo_root, output_path),
        "crop_count": matrix["crop_count"],
        "cell_count": matrix["cell_count"],
        "relationship_modes": matrix["relationship_modes"],
    }


def build_relationship_query_plan(
    repo_root: Path,
    *,
    crop_dir: str = DEFAULT_CROP_DIR,
    vocabulary_path: str = DEFAULT_VOCABULARY_PATH,
    mode_ids: Optional[Sequence[str]] = None,
    source_tier_policy_path: str = DEFAULT_SOURCE_TIER_POLICY_PATH,
    source_tier_policy_id: str = "",
    source_tier_ids: Optional[Sequence[str]] = None,
    queries_per_pair: int = 3,
    query_terms_per_source_tier: int = 3,
    max_pairs: Optional[int] = None,
    region_name: str = "global",
    include_self_pairs: bool = True,
) -> Dict[str, Any]:
    if queries_per_pair < 1:
        raise ValueError("queries_per_pair must be >= 1")
    crops = load_crop_universe(repo_root, crop_dir)
    vocabulary = load_relationship_vocabulary(repo_root, vocabulary_path)
    modes = selected_modes(vocabulary, mode_ids, all_when_unspecified=False)
    pairs = ordered_crop_pairs(crops, include_self_pairs=include_self_pairs)
    truncated = False
    if max_pairs is not None:
        if max_pairs < 1:
            raise ValueError("max_pairs must be >= 1")
        truncated = len(pairs) > max_pairs
        pairs = pairs[:max_pairs]
    source_tiers = selected_source_tiers(
        repo_root,
        {
            "source_tier_policy_path": source_tier_policy_path,
            "source_tier_policy_id": source_tier_policy_id,
            "source_tier_ids": list(source_tier_ids or []),
        },
    )
    if not source_tiers:
        source_tiers = [{"tier_id": "", "label": "", "query_terms": ["extension", "agronomy"]}]
    items: List[RelationshipQueryPlanItem] = []
    for subject, obj in pairs:
        for mode in modes:
            templates = relationship_templates_for_pair(mode, subject, obj)[:queries_per_pair]
            for template in templates:
                rendered = render_relationship_template(template["template"], subject, obj)
                for source_tier in source_tiers:
                    items.append(
                        RelationshipQueryPlanItem(
                            query=build_relationship_query(
                                rendered,
                                subject,
                                obj,
                                source_tier,
                                region_name=region_name,
                                query_terms_per_source_tier=query_terms_per_source_tier,
                            ),
                            subject_crop_id=subject.crop_id,
                            object_crop_id=obj.crop_id,
                            subject_crop_label=subject.label,
                            object_crop_label=obj.label,
                            relationship_mode=mode["mode_id"],
                            relationship_subtype=template["subtype"],
                            directionality=mode["directionality"],
                            ordered_pair_key=ordered_pair_key(subject.crop_id, obj.crop_id),
                            canonical_relationship_key=canonical_relationship_key(mode, subject.crop_id, obj.crop_id),
                            source_tier_id=source_tier.get("tier_id", ""),
                            source_tier_label=source_tier.get("label", ""),
                        )
                    )
    payload = {
        "version": "0.1.0",
        "generated_at": current_time(),
        "query_kind": "crop_relationship",
        "relationship_vocabulary_version": vocabulary["version"],
        "crop_count": len(crops),
        "matrix_cell_count": len(ordered_crop_pairs(crops, include_self_pairs=include_self_pairs)),
        "planned_pair_count": len(pairs),
        "relationship_modes": [mode["mode_id"] for mode in modes],
        "source_tier_ids": [source_tier.get("tier_id", "") for source_tier in source_tiers],
        "queries_per_pair": queries_per_pair,
        "query_count": len(items),
        "truncated": truncated,
        "queries": [item.to_json() for item in items],
    }
    SchemaRegistry(repo_root).validate("crop-relationship-query-plan.schema.json", payload)
    return payload


def write_relationship_query_plan(repo_root: Path, output_path: Path, **kwargs: Any) -> Dict[str, Any]:
    plan = build_relationship_query_plan(repo_root, **kwargs)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(plan, handle, indent=2)
        handle.write("\n")
    return {
        "query_plan_path": render_path(repo_root, output_path),
        "crop_count": plan["crop_count"],
        "matrix_cell_count": plan["matrix_cell_count"],
        "planned_pair_count": plan["planned_pair_count"],
        "relationship_modes": plan["relationship_modes"],
        "query_count": plan["query_count"],
        "truncated": plan["truncated"],
    }


def discover_relationships(
    repo_root: Path,
    run_id: str,
    *,
    crop_dir: str = DEFAULT_CROP_DIR,
    vocabulary_path: str = DEFAULT_VOCABULARY_PATH,
    mode_ids: Optional[Sequence[str]] = None,
    source_tier_policy_path: str = DEFAULT_SOURCE_TIER_POLICY_PATH,
    source_tier_policy_id: str = "",
    source_tier_ids: Optional[Sequence[str]] = None,
    queries_per_pair: int = 1,
    query_terms_per_source_tier: int = 3,
    max_pairs: Optional[int] = None,
    max_results_per_query: int = 5,
    region_name: str = "global",
    include_self_pairs: bool = True,
    limit_queries: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute relationship query planning into a durable discovery ledger.

    This is intentionally separate from parameter discovery. It preserves pair
    metadata on every row and deduplicates per (relationship key, source) so one
    source can support multiple crop-pair cells.
    """
    plan = build_relationship_query_plan(
        repo_root,
        crop_dir=crop_dir,
        vocabulary_path=vocabulary_path,
        mode_ids=mode_ids,
        source_tier_policy_path=source_tier_policy_path,
        source_tier_policy_id=source_tier_policy_id,
        source_tier_ids=source_tier_ids,
        queries_per_pair=queries_per_pair,
        query_terms_per_source_tier=query_terms_per_source_tier,
        max_pairs=max_pairs,
        region_name=region_name,
        include_self_pairs=include_self_pairs,
    )
    queries = list(plan["queries"])
    truncated_by_limit = False
    if limit_queries is not None:
        if limit_queries < 1:
            raise ValueError("limit_queries must be >= 1")
        truncated_by_limit = len(queries) > limit_queries
        queries = queries[:limit_queries]

    out_dir = repo_root / "exploration" / "relationships" / "discovery" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    configure_client(HttpClient(cache_dir=repo_root / "exploration" / "cache" / "providers"))

    rows: List[Dict[str, Any]] = []
    retry_queue: List[Dict[str, Any]] = []
    ua = user_agent()
    for item in queries:
        results, errors = connector_results_for_tier(
            query=item["query"],
            crop=item["subject_crop_label"].lower(),
            source_tier_id=item["source_tier_id"],
            max_results=max_results_per_query,
            user_agent=ua,
        )
        for error in errors:
            retry_queue.append(
                {
                    "query_kind": "crop_relationship",
                    "query": item["query"],
                    "subject_crop_id": item["subject_crop_id"],
                    "object_crop_id": item["object_crop_id"],
                    "relationship_mode": item["relationship_mode"],
                    "ordered_pair_key": item["ordered_pair_key"],
                    "canonical_relationship_key": item["canonical_relationship_key"],
                    "source_tier": item["source_tier_id"],
                    "error": error,
                }
            )
        provider_counts: Dict[str, int] = {}
        for result in results:
            provider = result.get("discovery_method", "unknown")
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
            metadata = result.get("source_metadata") or {}
            doi = metadata.get("doi", "")
            source_url = result.get("source_url", "")
            source_key = canonical_key(source_url, doi)
            rows.append(
                {
                    "ledger_id": relationship_ledger_id(run_id, len(rows) + 1),
                    "query_kind": "crop_relationship",
                    "query": item["query"],
                    "subject_crop_id": item["subject_crop_id"],
                    "object_crop_id": item["object_crop_id"],
                    "subject_crop_label": item["subject_crop_label"],
                    "object_crop_label": item["object_crop_label"],
                    "relationship_mode": item["relationship_mode"],
                    "relationship_subtype": item["relationship_subtype"],
                    "directionality": item["directionality"],
                    "ordered_pair_key": item["ordered_pair_key"],
                    "canonical_relationship_key": item["canonical_relationship_key"],
                    "source_tier": item["source_tier_id"],
                    "source_tier_label": item["source_tier_label"],
                    "provider": provider,
                    "discovery_rank": provider_counts[provider],
                    "score": result.get("score", 0),
                    "score_components": result.get("score_components", {}),
                    "source_url": source_url,
                    "source_key": source_key,
                    "relationship_source_key": "{0}|{1}".format(item["canonical_relationship_key"], source_key),
                    "doi": normalize_doi(doi),
                    "result_type": metadata.get("type", ""),
                    "access_status": result.get("access_status", "unknown"),
                    "source_domain": result.get("source_domain", urlparse(source_url).netloc.lower()),
                    "title": result.get("title", ""),
                    "discovery_drop_reason": "",
                }
            )

    stamp_relationship_drop_reasons(rows)
    write_jsonl(out_dir / "results.jsonl", rows)
    write_jsonl(out_dir / "retry_queue.jsonl", retry_queue)
    query_plan_path = out_dir / "query_plan.json"
    query_plan_payload = dict(plan)
    query_plan_payload["queries"] = queries
    query_plan_payload["query_count"] = len(queries)
    query_plan_payload["truncated"] = bool(plan["truncated"] or truncated_by_limit)
    query_plan_path.write_text(json.dumps(query_plan_payload, indent=2) + "\n", encoding="utf-8")
    summary = relationship_discovery_summary(run_id, query_plan_payload, rows, retry_queue, out_dir, query_plan_path)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def relationship_templates_for_pair(
    mode: Dict[str, Any],
    subject: CropNode,
    obj: CropNode,
) -> List[Dict[str, str]]:
    if subject.crop_id == obj.crop_id and mode.get("self_query_templates"):
        return list(mode["self_query_templates"])
    return list(mode["query_templates"])


def render_relationship_template(template: str, subject: CropNode, obj: CropNode) -> str:
    return (
        template.replace("{subject_crop}", subject.search_term)
        .replace("{object_crop}", obj.search_term)
        .replace("{subject_label}", subject.label)
        .replace("{object_label}", obj.label)
    )


def build_relationship_query(
    rendered_pattern: str,
    subject: CropNode,
    obj: CropNode,
    source_tier: Dict[str, Any],
    *,
    region_name: str,
    query_terms_per_source_tier: int,
) -> str:
    parts = [rendered_pattern]
    source_tier_id = source_tier.get("tier_id", "")
    if source_tier_id in {"peer_reviewed_science", "textbook_reference"}:
        parts.extend(first_scientific_names(subject, obj))
    if region_name.lower() != "global":
        parts.append(region_name)
    parts.extend((source_tier.get("query_terms") or [])[:query_terms_per_source_tier])
    return dedupe_words(" ".join(part for part in parts if part))


def first_scientific_names(subject: CropNode, obj: CropNode) -> Iterable[str]:
    names = []
    if subject.scientific_names:
        names.append(subject.scientific_names[0])
    if obj.crop_id != subject.crop_id and obj.scientific_names:
        names.append(obj.scientific_names[0])
    return names


def render_path(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def relationship_ledger_id(run_id: str, index: int) -> str:
    return "{0}-rel-led-{1:06d}".format(hashlib.sha1(run_id.encode()).hexdigest()[:6], index)


def stamp_relationship_drop_reasons(rows: List[Dict[str, Any]]) -> None:
    for row in rows:
        if row["provider"] in GATED_PROVIDERS and row["score"] < MIN_RELEVANCE_SCORE:
            row["discovery_drop_reason"] = "relevance_gate"
    best_by_relationship_source: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = row["relationship_source_key"]
        current = best_by_relationship_source.get(key)
        if current is None or row["score"] > current["score"]:
            best_by_relationship_source[key] = row
    for row in rows:
        if row is best_by_relationship_source.get(row["relationship_source_key"]):
            continue
        if not row["discovery_drop_reason"]:
            row["discovery_drop_reason"] = "duplicate"


def relationship_discovery_summary(
    run_id: str,
    plan: Dict[str, Any],
    rows: List[Dict[str, Any]],
    retry_queue: List[Dict[str, Any]],
    out_dir: Path,
    query_plan_path: Path,
) -> Dict[str, Any]:
    from collections import Counter

    drop_counts = Counter(row["discovery_drop_reason"] or "kept" for row in rows)
    return {
        "run_id": run_id,
        "stage": "relationship_discovery",
        "generated_at": current_time(),
        "query_kind": "crop_relationship",
        "relationship_modes": plan["relationship_modes"],
        "crop_count": plan["crop_count"],
        "matrix_cell_count": plan["matrix_cell_count"],
        "planned_pair_count": plan["planned_pair_count"],
        "queries_executed": plan["query_count"],
        "ledger_rows": len(rows),
        "unique_relationship_sources": len({row["relationship_source_key"] for row in rows}),
        "unique_sources": len({row["source_key"] for row in rows}),
        "provider_counts": dict(sorted(Counter(row["provider"] for row in rows).items())),
        "drop_reason_counts": dict(sorted(drop_counts.items())),
        "access_status_counts": dict(sorted(Counter(row["access_status"] for row in rows).items())),
        "retry_queue_size": len(retry_queue),
        "ledger_path": render_path(out_dir.parents[3], out_dir / "results.jsonl"),
        "query_plan_path": render_path(out_dir.parents[3], query_plan_path),
    }


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
