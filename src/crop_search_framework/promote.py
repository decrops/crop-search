from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .review import DURABLE_CANDIDATE_DECISIONS
from .schema_registry import SchemaRegistry


class PromotionRunner:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.registry = SchemaRegistry(repo_root)

    def promote_run(self, run_id: str) -> Dict[str, Any]:
        normalized_dir = self.repo_root / "exploration" / "normalized" / run_id
        review_path = self.repo_root / "exploration" / "review" / run_id / "review.json"
        if not normalized_dir.exists():
            raise FileNotFoundError("Normalized run not found: {0}".format(run_id))
        if not review_path.exists():
            raise FileNotFoundError("Review report not found: {0}".format(review_path))

        claims = self._load_claims(normalized_dir)
        review_report = self._load_json(review_path)
        self.registry.validate("claim-review.schema.json", review_report)

        claim_by_id = {claim["claim_id"]: claim for claim in claims}
        review_by_id = {review["claim_id"]: review for review in review_report["claim_reviews"]}
        promoted = []
        for claim_id in sorted(review_by_id):
            review = review_by_id[claim_id]
            if review["decision"] not in DURABLE_CANDIDATE_DECISIONS:
                continue
            claim = claim_by_id[claim_id]
            if claim.get("conflict_status") == "potential":
                continue
            promoted.append(promoted_claim_record(claim, review, len(promoted) + 1))

        report = {
            "run_id": run_id,
            "generated_at": capture_now(),
            "review_path": str(review_path.relative_to(self.repo_root)),
            "summary": build_summary(review_report["claim_reviews"], promoted),
            "promoted_claims": promoted,
        }
        self.registry.validate("durable-claims.schema.json", report)

        output_dir = self.repo_root / "memory" / "durable" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        claims_path = output_dir / "claims.json"
        summary_path = output_dir / "summary.json"
        with claims_path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
        with summary_path.open("w", encoding="utf-8") as handle:
            json.dump(report["summary"], handle, indent=2)
            handle.write("\n")

        return {
            "run_id": run_id,
            "claims_path": str(claims_path.relative_to(self.repo_root)),
            "summary_path": str(summary_path.relative_to(self.repo_root)),
            "summary": report["summary"],
        }

    def _load_claims(self, normalized_dir: Path) -> List[Dict[str, Any]]:
        claims = []
        for claim_file in sorted(path for path in normalized_dir.glob("*.json") if path.name != "summary.json"):
            claim = self._load_json(claim_file)
            self.registry.validate("normalized-claim.schema.json", claim)
            claims.append(claim)
        return claims

    def _load_json(self, path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)


def promoted_claim_record(claim: Dict[str, Any], review: Dict[str, Any], index: int) -> Dict[str, Any]:
    return {
        "durable_claim_id": "{0}-durable-{1:03d}".format(claim["run_id"], index),
        "source_claim_id": claim["claim_id"],
        "run_id": claim["run_id"],
        "entity": claim["entity"],
        "parameter_id": claim["parameter_id"],
        "attribute": claim["attribute"],
        "attribute_subtype": claim["attribute_subtype"],
        "claim_text": claim["claim_text"],
        "value": claim["value"],
        "location_scope": claim["location_scope"],
        "source_geo_scope": claim["source_geo_scope"],
        "geo_evidence": claim["geo_evidence"],
        "time_scope": claim["time_scope"],
        "provenance": claim["provenance"],
        "promotion_decision": review["decision"],
        "promotion_reasons": review["reasons"],
        "confidence": claim["confidence"],
        "status": "promoted",
    }


def build_summary(reviews: List[Dict[str, Any]], promoted: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "claims_considered": len(reviews),
        "promoted_claims": len(promoted),
        "decision_counts": sorted_counts(review["decision"] for review in reviews),
        "attribute_counts": sorted_counts(claim["attribute"] for claim in promoted),
        "parameter_counts": sorted_counts(claim["parameter_id"] for claim in promoted),
        "attribute_subtype_counts": sorted_counts(claim["attribute_subtype"] for claim in promoted),
    }


def sorted_counts(values: Iterable[str]) -> Dict[str, int]:
    counts = Counter(values)
    return {key: counts[key] for key in sorted(counts)}


def capture_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
