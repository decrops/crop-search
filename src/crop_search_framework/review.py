from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .quality import PREFERRED_DOMAIN_FRAGMENTS, PREFERRED_SOURCE_TERMS, claim_has_layout_artifact
from .schema_registry import SchemaRegistry


DURABLE_CANDIDATE_DECISIONS = {
    "canonical_candidate",
    "regional_observation",
    "merge_candidate",
}

# WS-9: source-tier precedence. Curated/peer-reviewed tiers outrank industry and
# background sources for the same parameter + scope. Used in addition to the
# domain-fragment heuristic so promotion can lean on the durable corpus tier.
SOURCE_TIER_WEIGHTS = {
    "peer_reviewed_science": 14,
    "international_institution": 12,
    "textbook_reference": 10,
    "extension_publication": 10,
    "industry_grower_guide": 4,
}
HIGH_TRUST_TIERS = {
    "peer_reviewed_science",
    "international_institution",
    "textbook_reference",
    "extension_publication",
}
# Secondary-synthesis signals: useful corroboration, but a primary measurement
# wins for canonical promotion (the reviews/meta-analyses WS-4 keeps).
SYNTHESIS_TITLE_PATTERNS = ("meta-analysis", "meta analysis", "systematic review", ": a review")

ARTIFACT_PATTERNS = (
    "all rights reserved",
    "date of planting 101-day",
    "h20 yield",
    "lsd0.05",
    "least significant difference",
    "table ",
    "figure ",
    "©",
)

SEASONAL_PATTERNS = (
    "as of ",
    "record-low",
    "below average",
    "field operations",
    "march ",
    "april ",
)


class ClaimReviewRunner:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.registry = SchemaRegistry(repo_root)

    def review_run(self, run_id: str) -> Dict[str, Any]:
        normalized_dir = self.repo_root / "exploration" / "normalized" / run_id
        if not normalized_dir.exists():
            raise FileNotFoundError("Normalized run not found: {0}".format(run_id))

        claims = self._load_claims(normalized_dir)
        clusters, claim_to_cluster = build_clusters(claims)
        reviews = [
            review_claim(claim, claim_to_cluster[claim["claim_id"]], clusters)
            for claim in claims
        ]
        source_scorecards = build_source_scorecards(claims, reviews)

        report = {
            "run_id": run_id,
            "generated_at": capture_now(),
            "input": self._input_summary(run_id, normalized_dir, claims),
            "summary": build_summary(claims, reviews, source_scorecards),
            "source_scorecards": source_scorecards,
            "clusters": cluster_records(clusters),
            "claim_reviews": reviews,
        }
        self.registry.validate("claim-review.schema.json", report)

        output_dir = self.repo_root / "exploration" / "review" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        review_path = output_dir / "review.json"
        summary_path = output_dir / "summary.json"
        with review_path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
        with summary_path.open("w", encoding="utf-8") as handle:
            json.dump(report["summary"], handle, indent=2)
            handle.write("\n")

        return {
            "run_id": run_id,
            "review_path": str(review_path.relative_to(self.repo_root)),
            "summary_path": str(summary_path.relative_to(self.repo_root)),
            "summary": report["summary"],
        }

    def _load_claims(self, normalized_dir: Path) -> List[Dict[str, Any]]:
        claim_files = sorted(path for path in normalized_dir.glob("*.json") if path.name != "summary.json")
        claims = []
        for claim_file in claim_files:
            claim = self._load_json(claim_file)
            self.registry.validate("normalized-claim.schema.json", claim)
            claims.append(claim)
        return claims

    def _input_summary(
        self,
        run_id: str,
        normalized_dir: Path,
        claims: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        raw_summary_path = self.repo_root / "exploration" / "raw" / run_id / "summary.json"
        normalized_summary_path = normalized_dir / "summary.json"
        input_summary: Dict[str, Any] = {
            "normalized_dir": str(normalized_dir.relative_to(self.repo_root)),
            "normalized_claims": len(claims),
        }
        if raw_summary_path.exists():
            raw_summary = self._load_json(raw_summary_path)
            input_summary["raw_summary_path"] = str(raw_summary_path.relative_to(self.repo_root))
            input_summary["raw_candidate_claims"] = raw_summary.get("candidate_claim_count", 0)
            input_summary["raw_failures"] = raw_summary.get("failure_count", 0)
        if normalized_summary_path.exists():
            input_summary["normalized_summary_path"] = str(normalized_summary_path.relative_to(self.repo_root))
        return input_summary

    def _load_json(self, path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)


def build_clusters(claims: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    grouped: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
    for claim in claims:
        grouped.setdefault(cluster_key(claim), []).append(claim)

    clusters: Dict[str, Dict[str, Any]] = {}
    claim_to_cluster: Dict[str, str] = {}
    for index, key in enumerate(sorted(grouped), start=1):
        cluster_id = "cluster-{0:03d}".format(index)
        group_claims = sorted(grouped[key], key=lambda item: item["claim_id"])
        clusters[cluster_id] = {
            "cluster_id": cluster_id,
            "key": key,
            "claims": group_claims,
        }
        for claim in group_claims:
            claim_to_cluster[claim["claim_id"]] = cluster_id
    return clusters, claim_to_cluster


def cluster_key(claim: Dict[str, Any]) -> Tuple[str, ...]:
    location = claim["location_scope"]
    return (
        claim["entity"]["name"].lower(),
        claim.get("parameter_id", ""),
        claim["attribute"],
        claim.get("attribute_subtype", claim["attribute"]),
        location["level"],
        location["name"].lower(),
        claim["time_scope"]["label"].lower(),
        value_signature(claim),
    )


def value_signature(claim: Dict[str, Any]) -> str:
    value = claim["value"]
    value_type = value["value_type"]
    if value_type == "numeric":
        numeric = value.get("normalized_numeric_value", value.get("numeric_value"))
        unit = value.get("normalized_unit", value.get("unit", ""))
        return "numeric:{0}:{1}".format(round(float(numeric), 4), unit)
    if value_type == "range":
        range_min = value.get("normalized_range_min", value.get("range_min"))
        range_max = value.get("normalized_range_max", value.get("range_max"))
        unit = value.get("normalized_unit", value.get("unit", ""))
        return "range:{0}:{1}:{2}".format(round(float(range_min), 4), round(float(range_max), 4), unit)
    text = value.get("text_value", value.get("raw_value_text", claim["claim_text"]))
    return "text:{0}".format(canonicalize_text(text))


def review_claim(
    claim: Dict[str, Any],
    cluster_id: str,
    clusters: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    quality_score, reasons = score_claim(claim)
    cluster_claims = clusters[cluster_id]["claims"]
    decision = decide_promotion(claim, quality_score, cluster_claims, reasons)
    provenance = claim["provenance"]
    value = claim["value"]
    return {
        "claim_id": claim["claim_id"],
        "cluster_id": cluster_id,
        "decision": decision,
        "quality_score": quality_score,
        "reasons": reasons,
        "parameter_id": claim.get("parameter_id", ""),
        "attribute": claim["attribute"],
        "attribute_subtype": claim.get("attribute_subtype", claim["attribute"]),
        "confidence": claim["confidence"],
        "conflict_status": claim.get("conflict_status", "none"),
        "source_domain": provenance["source_domain"],
        "source_title": provenance["source_title"],
        "document_type": provenance["document_type"],
        "value_type": value["value_type"],
        "claim_excerpt": truncate(claim["claim_text"], 240),
    }


def score_claim(claim: Dict[str, Any]) -> Tuple[int, List[str]]:
    score = 45
    reasons: List[str] = []
    provenance = claim["provenance"]
    value = claim["value"]
    lowered_claim = claim["claim_text"].lower()

    source_score, source_reasons = source_reliability_score(provenance)
    score += source_score
    reasons.extend(source_reasons)

    confidence = claim["confidence"]
    if confidence == "high":
        score += 20
        reasons.append("high extractor confidence")
    elif confidence == "medium":
        score += 8
        reasons.append("medium extractor confidence")
    else:
        score -= 14
        reasons.append("low extractor confidence")

    if value["value_type"] in {"numeric", "range"}:
        score += 16
        reasons.append("structured quantitative value")
    else:
        score -= 4
        reasons.append("text-only value")

    if value.get("normalized_unit"):
        score += 4
        reasons.append("normalized unit available")

    # WS-9: table/section provenance — values from a captioned table beat loose
    # prose. Block provenance is now durable on the claim (Phase D).
    if provenance.get("block_type") == "table":
        score += 6
        reasons.append("sourced from a table block")

    # WS-9: evidence specificity — reward a number adjacent to a unit in the
    # cited evidence (a concrete measurement, not vague prose).
    if has_specific_evidence(claim.get("evidence_text", "") or claim["claim_text"]):
        score += 5
        reasons.append("specific numeric evidence")

    if claim.get("conflict_status") == "potential":
        score -= 24
        reasons.append("potential quantitative conflict")

    if is_artifact_like(lowered_claim) or claim_has_layout_artifact(claim["claim_text"]):
        score -= 34
        reasons.append("table or layout artifact signal")

    if is_seasonal_observation(claim):
        score -= 4
        reasons.append("time-bound or seasonal observation")

    if has_publication_year(provenance):
        score += 3
        reasons.append("source publication year captured")

    return max(0, min(100, score)), unique_preserving_order(reasons)


def source_reliability_score(provenance: Dict[str, Any]) -> Tuple[int, List[str]]:
    score = 0
    reasons: List[str] = []
    domain = provenance.get("source_domain", "").lower()
    title = provenance.get("source_title", "").lower()
    source_text = "{0} {1}".format(domain, title)

    tier_id = provenance.get("source_tier_id", "")
    tier_bonus = SOURCE_TIER_WEIGHTS.get(tier_id, 0)
    if tier_bonus:
        score += tier_bonus
        reasons.append("source tier: {0}".format(tier_id))
    if any(fragment in domain for fragment in PREFERRED_DOMAIN_FRAGMENTS):
        score += 12
        reasons.append("preferred agronomy or extension domain")
    if any(term in source_text for term in PREFERRED_SOURCE_TERMS):
        score += 8
        reasons.append("preferred institutional source signal")
    if domain.endswith(".edu"):
        score += 5
        reasons.append("education domain")
    if provenance.get("document_type") == "pdf":
        score += 3
        reasons.append("PDF source document")
    return score, reasons


def decide_promotion(
    claim: Dict[str, Any],
    quality_score: int,
    cluster_claims: List[Dict[str, Any]],
    reasons: List[str],
) -> str:
    conflict_status = claim.get("conflict_status", "none")
    value_type = claim["value"]["value_type"]
    provenance = claim["provenance"]
    has_duplicate_sources = len({item["provenance"]["source_domain"] for item in cluster_claims}) > 1
    high_tier = provenance.get("source_tier_id", "") in HIGH_TRUST_TIERS
    is_synthesis = any(p in provenance.get("source_title", "").lower() for p in SYNTHESIS_TITLE_PATTERNS)

    if "table or layout artifact signal" in reasons or quality_score < 35:
        return "reject"
    if conflict_status == "potential":
        return "needs_review"
    if is_seasonal_observation(claim):
        return "seasonal_observation"
    if has_duplicate_sources and quality_score >= 70:
        return "merge_candidate"
    if is_regional_observation(claim) and quality_score >= 62:
        return "regional_observation"
    # WS-9: a secondary synthesis (meta-analysis/review) corroborates but a
    # primary measurement wins for canonical — never promote a synthesis.
    if value_type in {"numeric", "range"} and not is_synthesis:
        # Tier precedence lowers the canonical bar for trustworthy primary
        # sources (draining needs_review).
        if high_tier and quality_score >= 66:
            return "canonical_candidate"
        if quality_score >= 72:
            return "canonical_candidate"
    if quality_score >= 50:
        return "needs_review"
    return "reject"


def has_specific_evidence(text: str) -> bool:
    """True if the evidence has a number adjacent to a unit/symbol — a concrete
    measurement rather than vague prose."""
    return bool(
        re.search(r"\d+(?:\.\d+)?\s*(?:%|°|kg|lb|mm|cm|ha|dS|gdd|bu|days?|c\b|f\b|/)", text.lower())
    )


def is_artifact_like(lowered_claim: str) -> bool:
    return any(pattern in lowered_claim for pattern in ARTIFACT_PATTERNS)


def is_seasonal_observation(claim: Dict[str, Any]) -> bool:
    lowered_claim = claim["claim_text"].lower()
    time_label = claim["time_scope"]["label"].lower()
    publication_year = claim["provenance"].get("source_publication_year")
    if time_label == "observed study period":
        return True
    if publication_year is not None and publication_year >= 2023:
        return True
    if re.search(r"\b20(1[7-9]|2[0-6])\b", lowered_claim):
        return True
    return any(pattern in lowered_claim for pattern in SEASONAL_PATTERNS)


def is_regional_observation(claim: Dict[str, Any]) -> bool:
    lowered_text = " ".join(
        [
            claim["claim_text"],
            claim["provenance"].get("source_title", ""),
            claim["location_scope"].get("name", ""),
        ]
    ).lower()
    return "iowa" in lowered_text or claim["location_scope"]["level"] in {
        "country",
        "region",
        "state",
        "county",
        "farm",
    }


def has_publication_year(provenance: Dict[str, Any]) -> bool:
    return provenance.get("source_publication_year") is not None


def build_source_scorecards(
    claims: List[Dict[str, Any]],
    reviews: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    review_by_claim_id = {review["claim_id"]: review for review in reviews}
    grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for claim in claims:
        provenance = claim["provenance"]
        key = (
            provenance["source_domain"],
            provenance["source_title"],
            provenance["document_type"],
        )
        grouped.setdefault(key, []).append(claim)

    scorecards = []
    for key, source_claims in grouped.items():
        source_reviews = [review_by_claim_id[claim["claim_id"]] for claim in source_claims]
        scores = [review["quality_score"] for review in source_reviews]
        decision_counts = sorted_counts(review["decision"] for review in source_reviews)
        average_score = round(sum(scores) / len(scores), 2) if scores else 0
        scorecards.append(
            {
                "source_domain": key[0],
                "source_title": key[1],
                "document_type": key[2],
                "claim_count": len(source_claims),
                "average_quality_score": average_score,
                "reliability_tier": source_tier(average_score, decision_counts),
                "decision_counts": decision_counts,
                "attribute_counts": sorted_counts(claim["attribute"] for claim in source_claims),
                "parameter_counts": sorted_counts(claim.get("parameter_id", "") for claim in source_claims),
                "attribute_subtype_counts": sorted_counts(
                    claim.get("attribute_subtype", claim["attribute"]) for claim in source_claims
                ),
                "reasons": source_reasons(source_reviews),
            }
        )
    return sorted(scorecards, key=lambda item: (-item["average_quality_score"], item["source_domain"]))


def source_tier(average_score: float, decision_counts: Dict[str, int]) -> str:
    if average_score >= 70 and decision_counts.get("reject", 0) == 0:
        return "strong"
    if average_score >= 50:
        return "mixed"
    return "weak"


def source_reasons(reviews: List[Dict[str, Any]]) -> List[str]:
    reason_counts = Counter(reason for review in reviews for reason in review["reasons"])
    return [reason for reason, _ in reason_counts.most_common(5)]


def cluster_records(clusters: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    records = []
    for cluster_id in sorted(clusters):
        cluster = clusters[cluster_id]
        claims = cluster["claims"]
        key = cluster["key"]
        records.append(
            {
                "cluster_id": cluster_id,
                "parameter_id": key[1],
                "attribute": key[2],
                "attribute_subtype": key[3],
                "scope": "{0}:{1}:{2}".format(key[4], key[5], key[6]),
                "value_signature": key[7],
                "claim_ids": [claim["claim_id"] for claim in claims],
                "source_domains": sorted({claim["provenance"]["source_domain"] for claim in claims}),
                "recommended_action": cluster_action(claims),
            }
        )
    return records


def cluster_action(claims: List[Dict[str, Any]]) -> str:
    if any(claim.get("conflict_status") == "potential" for claim in claims):
        return "manual_conflict_review"
    if len({claim["provenance"]["source_domain"] for claim in claims}) > 1:
        return "merge_duplicate_claims"
    return "single_claim_review"


def build_summary(
    claims: List[Dict[str, Any]],
    reviews: List[Dict[str, Any]],
    source_scorecards: List[Dict[str, Any]],
) -> Dict[str, Any]:
    decision_counts = sorted_counts(review["decision"] for review in reviews)
    return {
        "claims_reviewed": len(reviews),
        "durable_candidate_claims": sum(
            count for decision, count in decision_counts.items() if decision in DURABLE_CANDIDATE_DECISIONS
        ),
        "manual_review_claims": decision_counts.get("needs_review", 0),
        "rejected_claims": decision_counts.get("reject", 0),
        "seasonal_observation_claims": decision_counts.get("seasonal_observation", 0),
        "decision_counts": decision_counts,
        "attribute_counts": sorted_counts(claim["attribute"] for claim in claims),
        "parameter_counts": sorted_counts(claim.get("parameter_id", "") for claim in claims),
        "attribute_subtype_counts": sorted_counts(
            claim.get("attribute_subtype", claim["attribute"]) for claim in claims
        ),
        "confidence_counts": sorted_counts(claim["confidence"] for claim in claims),
        "conflict_counts": sorted_counts(claim.get("conflict_status", "none") for claim in claims),
        "source_count": len(source_scorecards),
    }


def sorted_counts(values: Iterable[str]) -> Dict[str, int]:
    counts = Counter(values)
    return {key: counts[key] for key in sorted(counts)}


def canonicalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def truncate(value: str, max_length: int) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3].rstrip() + "..."


def unique_preserving_order(values: Iterable[str]) -> List[str]:
    seen = set()
    unique = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def capture_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
