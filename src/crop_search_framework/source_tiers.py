from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .schema_registry import SchemaRegistry


def load_source_tier_manifest(repo_root: Path, manifest_path: str) -> Dict[str, Any]:
    path = resolve_repo_path(repo_root, manifest_path)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    SchemaRegistry(repo_root).validate("source-tier-manifest.schema.json", payload)
    return payload


def resolve_repo_path(repo_root: Path, path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else repo_root / path


def selected_source_tiers(repo_root: Path, run_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    manifest_path = run_config.get("source_tier_policy_path", "")
    if not manifest_path:
        return []
    manifest = load_source_tier_manifest(repo_root, manifest_path)
    tiers_by_id = {tier["tier_id"]: tier for tier in manifest["tiers"]}
    requested_ids = run_config.get("source_tier_ids", [])
    if not requested_ids:
        policy_id = run_config.get("source_tier_policy_id") or manifest["default_policy_id"]
        policy = next((item for item in manifest["policies"] if item["policy_id"] == policy_id), None)
        if not policy:
            raise ValueError("Unknown source tier policy: {0}".format(policy_id))
        requested_ids = policy["tier_order"]
    missing_ids = [tier_id for tier_id in requested_ids if tier_id not in tiers_by_id]
    if missing_ids:
        raise ValueError("Unknown source tier ids: {0}".format(", ".join(missing_ids)))
    return [tiers_by_id[tier_id] for tier_id in requested_ids]


def source_tier_score_bonus(title: str, snippet: str, domain: str, url: str) -> int:
    lowered = " ".join([title, snippet, domain, url]).lower()
    best_bonus = 0
    for tier in DEFAULT_TIER_SIGNALS:
        tier_bonus = 0
        if any(fragment in domain.lower() or fragment in url.lower() for fragment in tier["domain_fragments"]):
            tier_bonus += int(tier["domain_bonus"])
        if any(term.lower() in lowered for term in tier["title_terms"]):
            tier_bonus += int(tier["term_bonus"])
        if tier_bonus > best_bonus:
            best_bonus = tier_bonus
    return min(best_bonus, 8)


DEFAULT_TIER_SIGNALS = [
    {
        "tier_id": "peer_reviewed_science",
        "domain_bonus": 6,
        "term_bonus": 4,
        "domain_fragments": (
            "doi.org",
            "sciencedirect.com",
            "springer.com",
            "springerlink.com",
            "tandfonline.com",
            "wiley.com",
            "onlinelibrary.wiley.com",
            "frontiersin.org",
            "mdpi.com",
            "plos.org",
            "nature.com",
            "academic.oup.com",
            "cambridge.org",
            "pubmed.ncbi.nlm.nih.gov",
            "ncbi.nlm.nih.gov",
            "semanticscholar.org",
        ),
        "title_terms": (
            "doi",
            "journal",
            "peer reviewed",
            "field experiment",
            "meta-analysis",
            "crop physiology",
            "temperature response",
            "water stress",
        ),
    },
    {
        "tier_id": "textbook_reference",
        "domain_bonus": 5,
        "term_bonus": 4,
        "domain_fragments": (
            "books.google.com",
            "archive.org",
            "openlibrary.org",
            "nap.nationalacademies.org",
            "cabidigitallibrary.org",
        ),
        "title_terms": (
            "textbook",
            "crop physiology",
            "crop production",
            "reference",
            "handbook",
        ),
    },
    {
        "tier_id": "international_institution",
        "domain_bonus": 5,
        "term_bonus": 4,
        "domain_fragments": (
            "fao.org",
            "cgiar.org",
            "cimmyt.org",
            "irri.org",
            "icrisat.org",
            "africarice.org",
            "worldveg.org",
            "cabi.org",
            "knowledgebank.irri.org",
        ),
        "title_terms": (
            "FAO",
            "CGIAR",
            "CIMMYT",
            "IRRI",
            "ICRISAT",
            "production manual",
            "crop calendar",
            "knowledge bank",
        ),
    },
    {
        "tier_id": "extension_publication",
        "domain_bonus": 4,
        "term_bonus": 3,
        "domain_fragments": (
            ".edu",
            ".gov",
            "extension",
            "agriculture",
            "agronomy",
            "grdc.com.au",
            "ahdb.org.uk",
            "agric.wa.gov.au",
            "dpi.nsw.gov.au",
        ),
        "title_terms": (
            "extension",
            "agronomy",
            "production guide",
            "management guide",
            "crop guide",
            "handbook",
        ),
    },
    {
        "tier_id": "industry_grower_guide",
        "domain_bonus": 2,
        "term_bonus": 3,
        "domain_fragments": (
            "seed",
            "crop",
            "agronomy",
            "grower",
            "commodity",
        ),
        "title_terms": (
            "grower guide",
            "agronomy guide",
            "management recommendations",
            "production recommendations",
        ),
    },
]
