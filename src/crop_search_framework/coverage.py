from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .parameters import load_crop_profile, load_parameter_manifest, query_plan_for_run, selected_parameters
from .schema_registry import SchemaRegistry


class ParameterCoverageRunner:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.registry = SchemaRegistry(repo_root)

    def coverage_run(self, run_id: str) -> Dict[str, Any]:
        run_config = self._run_config_for_id(run_id)
        if not run_config.get("parameter_manifest_path") or not run_config.get("crop_profile_path"):
            raise ValueError("Run does not declare parameter_manifest_path and crop_profile_path")

        manifest = load_parameter_manifest(self.repo_root, run_config["parameter_manifest_path"])
        crop_profile = load_crop_profile(self.repo_root, run_config["crop_profile_path"])
        requested = selected_parameters(run_config, manifest, crop_profile)
        raw_summary = self._load_optional_json(self.repo_root / "exploration" / "raw" / run_id / "summary.json")
        raw_captures = self._load_raw_captures(run_id)
        normalized_claims = self._load_normalized_claims(run_id)
        normalized_by_id = {claim["claim_id"]: claim for claim in normalized_claims}
        review_report = self._load_optional_json(self.repo_root / "exploration" / "review" / run_id / "review.json")
        durable_report = self._load_optional_json(self.repo_root / "memory" / "durable" / run_id / "claims.json")

        normalized_counts = Counter(claim.get("parameter_id", "") for claim in normalized_claims)
        normalized_tier_counts = Counter(claim_tier_key(claim) for claim in normalized_claims)
        normalized_parameter_tier_counts = Counter(
            (claim.get("parameter_id", ""), claim_tier_key(claim)) for claim in normalized_claims
        )
        review_counts = Counter()
        review_tier_counts = Counter()
        review_parameter_tier_counts = Counter()
        seasonal_counts = Counter()
        for review in review_report.get("claim_reviews", []):
            parameter_id = review.get("parameter_id", "")
            if review["decision"] == "needs_review":
                review_counts[parameter_id] += 1
                tier_id = claim_tier_key(normalized_by_id.get(review["claim_id"], {}))
                review_tier_counts[tier_id] += 1
                review_parameter_tier_counts[(parameter_id, tier_id)] += 1
            if review["decision"] == "seasonal_observation":
                seasonal_counts[parameter_id] += 1

        promoted_counts = Counter(claim.get("parameter_id", "") for claim in durable_report.get("promoted_claims", []))
        promoted_tier_counts = Counter(claim_tier_key(claim) for claim in durable_report.get("promoted_claims", []))
        promoted_parameter_tier_counts = Counter(
            (claim.get("parameter_id", ""), claim_tier_key(claim))
            for claim in durable_report.get("promoted_claims", [])
        )
        query_plan = query_plan_for_run(self.repo_root, run_config)
        query_counts = Counter(item.parameter_id for item in query_plan)
        query_tier_counts = Counter(source_tier_key(item.source_tier_id) for item in query_plan)
        query_parameter_tier_counts = Counter((item.parameter_id, source_tier_key(item.source_tier_id)) for item in query_plan)
        source_tier_labels = source_tier_labels_for_plan(query_plan)

        parameter_records = []
        for parameter in requested:
            parameter_id = parameter["parameter_id"]
            source_tier_counts = parameter_source_tier_counts(
                parameter_id,
                source_tier_labels,
                query_parameter_tier_counts,
                normalized_parameter_tier_counts,
                promoted_parameter_tier_counts,
                review_parameter_tier_counts,
            )
            parameter_records.append(
                {
                    "parameter_id": parameter_id,
                    "label": parameter["label"],
                    "family": parameter["family"],
                    "category": parameter["category"],
                    "status": coverage_status(
                        normalized_counts[parameter_id],
                        promoted_counts[parameter_id],
                        review_counts[parameter_id],
                    ),
                    "normalized_claim_count": normalized_counts[parameter_id],
                    "promoted_claim_count": promoted_counts[parameter_id],
                    "needs_review_count": review_counts[parameter_id],
                    "seasonal_observation_count": seasonal_counts[parameter_id],
                    "query_count": query_counts[parameter_id],
                    "source_tier_counts": source_tier_counts,
                    "science_textbook_status": science_textbook_status(source_tier_counts),
                }
            )

        source_tier_summary = build_source_tier_summary(
            raw_summary=raw_summary,
            raw_captures=raw_captures,
            source_tier_labels=source_tier_labels,
            query_tier_counts=query_tier_counts,
            normalized_tier_counts=normalized_tier_counts,
            promoted_tier_counts=promoted_tier_counts,
            review_tier_counts=review_tier_counts,
        )
        report = {
            "run_id": run_id,
            "generated_at": capture_now(),
            "parameter_manifest_path": run_config["parameter_manifest_path"],
            "crop_profile_path": run_config["crop_profile_path"],
            "summary": build_summary(parameter_records),
            "source_tier_summary": source_tier_summary,
            "parameters": parameter_records,
        }
        self.registry.validate("parameter-coverage.schema.json", report)

        output_dir = self.repo_root / "exploration" / "coverage" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        coverage_path = output_dir / "coverage.json"
        summary_path = output_dir / "summary.json"
        with coverage_path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
        with summary_path.open("w", encoding="utf-8") as handle:
            json.dump(report["summary"], handle, indent=2)
            handle.write("\n")
        return {
            "run_id": run_id,
            "coverage_path": str(coverage_path.relative_to(self.repo_root)),
            "summary_path": str(summary_path.relative_to(self.repo_root)),
            "summary": report["summary"],
        }

    def _run_config_for_id(self, run_id: str) -> Dict[str, Any]:
        for path in (self.repo_root / "config" / "runs").glob("*.json"):
            payload = self._load_json(path)
            if payload.get("run_id") == run_id:
                self.registry.validate("exploration-run.schema.json", payload)
                return payload
        raise FileNotFoundError("Run config not found for run_id: {0}".format(run_id))

    def _load_normalized_claims(self, run_id: str) -> List[Dict[str, Any]]:
        normalized_dir = self.repo_root / "exploration" / "normalized" / run_id
        claims = []
        for path in sorted(item for item in normalized_dir.glob("*.json") if item.name != "summary.json"):
            claim = self._load_json(path)
            self.registry.validate("normalized-claim.schema.json", claim)
            claims.append(claim)
        return claims

    def _load_raw_captures(self, run_id: str) -> List[Dict[str, Any]]:
        raw_dir = self.repo_root / "exploration" / "raw" / run_id
        captures = []
        for path in sorted(item for item in raw_dir.glob("*.json") if item.name != "summary.json"):
            capture = self._load_json(path)
            self.registry.validate("raw-capture.schema.json", capture)
            captures.append(capture)
        return captures

    def _load_optional_json(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        return self._load_json(path)

    def _load_json(self, path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)


def coverage_status(normalized_count: int, promoted_count: int, needs_review_count: int) -> str:
    if promoted_count:
        return "promoted"
    if needs_review_count:
        return "needs_review"
    if normalized_count:
        return "candidate_only"
    return "missing"


def build_summary(parameters: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "parameters_requested": len(parameters),
        "parameters_with_normalized_claims": sum(1 for item in parameters if item["normalized_claim_count"] > 0),
        "parameters_with_promoted_claims": sum(1 for item in parameters if item["promoted_claim_count"] > 0),
        "parameters_missing": sum(1 for item in parameters if item["status"] == "missing"),
        "parameters_needing_review": sum(1 for item in parameters if item["status"] == "needs_review"),
        "parameters_with_peer_reviewed_claims": sum(
            1 for item in parameters if tier_has_claims(item, "peer_reviewed_science")
        ),
        "parameters_with_textbook_claims": sum(1 for item in parameters if tier_has_claims(item, "textbook_reference")),
        "parameters_with_peer_reviewed_or_textbook_claims": sum(
            1
            for item in parameters
            if item["science_textbook_status"]
            in {"promoted_science_or_textbook", "candidate_science_or_textbook"}
        ),
    }


def sorted_counts(values: Iterable[str]) -> Dict[str, int]:
    counts = Counter(values)
    return {key: counts[key] for key in sorted(counts)}


def source_tier_key(source_tier_id: str) -> str:
    return source_tier_id or "unspecified"


def claim_tier_key(claim: Dict[str, Any]) -> str:
    provenance = claim.get("provenance", {})
    return source_tier_key(provenance.get("source_tier_id", ""))


def source_tier_labels_for_plan(query_plan: List[Any]) -> Dict[str, str]:
    labels = {}
    for item in query_plan:
        tier_id = source_tier_key(item.source_tier_id)
        labels[tier_id] = item.source_tier_label or ("Unspecified" if tier_id == "unspecified" else tier_id)
    return labels


def parameter_source_tier_counts(
    parameter_id: str,
    source_tier_labels: Dict[str, str],
    query_parameter_tier_counts: Counter,
    normalized_parameter_tier_counts: Counter,
    promoted_parameter_tier_counts: Counter,
    review_parameter_tier_counts: Counter,
) -> List[Dict[str, Any]]:
    tier_ids = set(source_tier_labels)
    tier_ids.update(tier_id for item_parameter_id, tier_id in normalized_parameter_tier_counts if item_parameter_id == parameter_id)
    tier_ids.update(tier_id for item_parameter_id, tier_id in promoted_parameter_tier_counts if item_parameter_id == parameter_id)
    tier_ids.update(tier_id for item_parameter_id, tier_id in review_parameter_tier_counts if item_parameter_id == parameter_id)
    records = []
    for tier_id in sorted(tier_ids):
        records.append(
            {
                "source_tier_id": "" if tier_id == "unspecified" else tier_id,
                "source_tier_label": source_tier_labels.get(tier_id, "Unspecified" if tier_id == "unspecified" else tier_id),
                "query_count": query_parameter_tier_counts[(parameter_id, tier_id)],
                "normalized_claim_count": normalized_parameter_tier_counts[(parameter_id, tier_id)],
                "promoted_claim_count": promoted_parameter_tier_counts[(parameter_id, tier_id)],
                "needs_review_count": review_parameter_tier_counts[(parameter_id, tier_id)],
            }
        )
    return records


def science_textbook_status(source_tier_counts: List[Dict[str, Any]]) -> str:
    science_tiers = {"peer_reviewed_science", "textbook_reference"}
    science_records = [record for record in source_tier_counts if record["source_tier_id"] in science_tiers]
    if any(record["promoted_claim_count"] for record in science_records):
        return "promoted_science_or_textbook"
    if any(record["normalized_claim_count"] or record["needs_review_count"] for record in science_records):
        return "candidate_science_or_textbook"
    if any(record["normalized_claim_count"] or record["promoted_claim_count"] for record in source_tier_counts):
        return "extension_or_other_only"
    return "missing"


def tier_has_claims(parameter_record: Dict[str, Any], source_tier_id: str) -> bool:
    return any(
        record["source_tier_id"] == source_tier_id
        and (record["normalized_claim_count"] > 0 or record["promoted_claim_count"] > 0)
        for record in parameter_record["source_tier_counts"]
    )


def build_source_tier_summary(
    raw_summary: Dict[str, Any],
    raw_captures: List[Dict[str, Any]],
    source_tier_labels: Dict[str, str],
    query_tier_counts: Counter,
    normalized_tier_counts: Counter,
    promoted_tier_counts: Counter,
    review_tier_counts: Counter,
) -> List[Dict[str, Any]]:
    search_result_counts = Counter()
    total_result_counts = Counter()
    for query_summary in raw_summary.get("query_summaries", []):
        tier_id = source_tier_key(query_summary.get("source_tier_id", ""))
        search_result_counts[tier_id] += query_summary.get("search_results_returned", 0)
        total_result_counts[tier_id] += query_summary.get("results_returned", 0)

    capture_counts = Counter(source_tier_key(capture.get("source_tier_id", "")) for capture in raw_captures)
    open_text_counts = Counter(
        source_tier_key(capture.get("source_tier_id", ""))
        for capture in raw_captures
        if capture.get("access_status", "unknown") == "open_full_text"
    )
    metadata_counts = Counter(
        source_tier_key(capture.get("source_tier_id", ""))
        for capture in raw_captures
        if capture.get("access_status", "unknown") == "metadata_only"
    )
    candidate_claim_counts = Counter()
    access_status_counts: Dict[str, Counter] = {}
    discovery_method_counts: Dict[str, Counter] = {}
    for capture in raw_captures:
        tier_id = source_tier_key(capture.get("source_tier_id", ""))
        candidate_claim_counts[tier_id] += len(capture.get("candidate_claims", []))
        access_status_counts.setdefault(tier_id, Counter())[capture.get("access_status", "unknown")] += 1
        discovery_method_counts.setdefault(tier_id, Counter())[capture.get("discovery_method", "unknown")] += 1

    tier_ids = set(source_tier_labels) | set(query_tier_counts) | set(capture_counts) | set(normalized_tier_counts) | set(promoted_tier_counts)
    records = []
    for tier_id in sorted(tier_ids):
        records.append(
            {
                "source_tier_id": "" if tier_id == "unspecified" else tier_id,
                "source_tier_label": source_tier_labels.get(tier_id, "Unspecified" if tier_id == "unspecified" else tier_id),
                "query_count": query_tier_counts[tier_id],
                "search_result_count": search_result_counts[tier_id],
                "total_result_count": total_result_counts[tier_id],
                "captured_source_count": capture_counts[tier_id],
                "open_full_text_capture_count": open_text_counts[tier_id],
                "metadata_only_capture_count": metadata_counts[tier_id],
                "candidate_claim_count": candidate_claim_counts[tier_id],
                "normalized_claim_count": normalized_tier_counts[tier_id],
                "promoted_claim_count": promoted_tier_counts[tier_id],
                "needs_review_count": review_tier_counts[tier_id],
                "access_status_counts": dict_sorted_counts(access_status_counts.get(tier_id, Counter())),
                "discovery_method_counts": dict_sorted_counts(discovery_method_counts.get(tier_id, Counter())),
            }
        )
    return records


def dict_sorted_counts(counts: Counter) -> Dict[str, int]:
    return {key: counts[key] for key in sorted(counts)}


def capture_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
