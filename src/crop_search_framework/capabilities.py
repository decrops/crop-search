from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .parameters import query_plan_for_run
from .relationships import build_relationship_matrix
from .schema_registry import SchemaRegistry


class CapabilityMapWriter:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.registry = SchemaRegistry(repo_root)

    def write(self, output_path: Path) -> Dict[str, Any]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        content = self.render()
        output_path.write_text(content, encoding="utf-8")
        return {
            "capability_map_path": str(output_path.relative_to(self.repo_root)),
            "generated_at": current_time(),
        }

    def render(self) -> str:
        generated_at = current_time()
        global_query_counts = self.global_query_counts()
        artifact_totals = self.artifact_totals()
        global_artifact_totals = self.global_artifact_totals()
        parameter_catalog = self.parameter_catalog_summary()
        relationship_matrix = self.relationship_matrix_summary()
        capabilities = self.capabilities(
            global_query_counts,
            artifact_totals,
            global_artifact_totals,
            parameter_catalog,
            relationship_matrix,
        )

        lines = [
            "# Capability Map",
            "",
            "Generated at: `{0}`".format(generated_at),
            "",
            "This map is the living inventory of what the crop-search pipeline can do, what is only partially working, and what is not implemented yet. Future sessions should update it with `PYTHONPATH=src python3 -m crop_search_framework.cli write-capability-map` whenever capabilities change.",
            "",
            "## Status Legend",
            "",
            "- `operational`: implemented and exercised against current artifacts or tests.",
            "- `configured`: implemented enough to run, but not yet exercised end to end with fresh live data.",
            "- `partial`: works for a constrained subset or has known quality gaps.",
            "- `missing`: not implemented or not production-usable.",
            "",
            "## Summary",
            "",
            "| Area | Status | Can Do Now | Still Missing | Evidence |",
            "| --- | --- | --- | --- | --- |",
        ]
        for capability in capabilities:
            lines.append(
                "| {area} | `{status}` | {can_do} | {missing} | {evidence} |".format(**capability)
            )

        lines.extend(
            [
                "",
                "## Current Artifact Totals",
                "",
                "| Metric | Value |",
                "| --- | ---: |",
                "| Core parameter catalog parameters | {0} |".format(parameter_catalog["count"]),
                "| Core parameter catalog families | {0} |".format(parameter_catalog["family_count"]),
                "| U.S. pilot raw candidate claims | {0} |".format(artifact_totals["raw_candidate_claims"]),
                "| U.S. pilot normalized claims | {0} |".format(artifact_totals["normalized_claims"]),
                "| U.S. pilot promoted durable claims | {0} |".format(artifact_totals["promoted_claims"]),
                "| U.S. pilot manual-review claims | {0} |".format(artifact_totals["manual_review_claims"]),
                "| Global pilot runs executed | {0} |".format(global_artifact_totals["executed_runs"]),
                "| Global pilot raw candidate claims | {0} |".format(global_artifact_totals["raw_candidate_claims"]),
                "| Global pilot normalized claims | {0} |".format(global_artifact_totals["normalized_claims"]),
                "| Global pilot promoted durable claims | {0} |".format(global_artifact_totals["promoted_claims"]),
                "| Global pilot manual-review claims | {0} |".format(global_artifact_totals["manual_review_claims"]),
                "| Global pilot peer-reviewed normalized claims | {0} |".format(global_artifact_totals["peer_reviewed_normalized_claims"]),
                "| Global pilot textbook/reference normalized claims | {0} |".format(global_artifact_totals["textbook_normalized_claims"]),
                "| Global tier-aware run configs | {0} |".format(len(global_query_counts)),
                "| Planned global tier-aware queries | {0} |".format(sum(global_query_counts.values())),
                "",
                "## Global Query Plans",
                "",
                "| Run config | Planned queries |",
                "| --- | ---: |",
            ]
        )
        for path, query_count in global_query_counts.items():
            lines.append("| `{0}` | {1} |".format(path, query_count))

        lines.extend(
            [
                "",
                "## Update Rule",
                "",
                "Update this map when any of these change:",
                "",
                "- New pipeline stage, source tier, parser, normalizer, review rule, loader, or geocoder capability.",
                "- A capability moves between `missing`, `partial`, `configured`, and `operational`.",
                "- New live run results materially change known quality or coverage.",
                "- New constraints are discovered.",
                "",
                "Refresh command:",
                "",
                "```bash",
                "PYTHONPATH=src python3 -m crop_search_framework.cli write-capability-map",
                "PYTHONPATH=src python3 -m crop_search_framework.cli write-handoff",
                "```",
                "",
            ]
        )
        return "\n".join(lines)

    def capabilities(
        self,
        global_query_counts: Dict[str, int],
        artifact_totals: Dict[str, int],
        global_artifact_totals: Dict[str, int],
        parameter_catalog: Dict[str, Any],
        relationship_matrix: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        return [
            capability(
                "Context continuity",
                "operational",
                "Maintains `AGENTS.md`, generated `docs/HANDOFF.md`, and this capability map.",
                "No git history is available in this workspace, so docs remain the main memory mechanism.",
                "`AGENTS.md`, `docs/HANDOFF.md`, `docs/CAPABILITY_MAP.md`",
            ),
            capability(
                "Parameter manifest",
                "operational",
                "Defines {0} reusable crop physiology, phenology, canopy, root, water, soil, nutrient, stress, establishment, quality, harvest, and management parameters and maps them into crop-specific query plans.".format(parameter_catalog["count"]),
                "Extraction, normalization, and promotion rules do not yet semantically cover every expanded trait family.",
                "`config/parameters/core-crop-parameters.json` v{0}; {1} families".format(parameter_catalog["version"], parameter_catalog["family_count"]),
            ),
            capability(
                "Crop profiles",
                "operational",
                "Supports corn, soybean, wheat, rice, cotton, sunflower, and tomato profiles.",
                "More crops require new profile JSON files and source-bias terms.",
                "`config/crops/`",
            ),
            capability(
                "Crop relationship matrix",
                "configured",
                "Generates a dense `crop_id x crop_id` relationship matrix skeleton, source-tier-aware relationship query plans, and opt-in relationship discovery ledgers; the current {0}-crop universe produces {1} ordered cells and preserves symmetric evidence with canonical relationship keys. Matrix cells are populated from validated relationship claims.".format(
                    relationship_matrix["crop_count"],
                    relationship_matrix["cell_count"],
                ),
                "Live relationship fetch execution, in-session Opus extraction, review, and human acceptance remain manual gates.",
                "`src/crop_search_framework/relationships.py`, `config/relationships/relationship-vocabulary.json`, `schemas/crop-relationship-*.schema.json`",
            ),
            capability(
                "Hybrid relationship evidence graph",
                "configured",
                "Layers a request-time evidence graph over the dense matrix for minor crops and aggregate nodes: `--pair-mode auto` plans unordered for symmetric modes / ordered otherwise (override with ordered|unordered); `--node-mode aggregate` plans group-level (family/functional-group/host-group) searches from the node catalog, steered to textbook/institution/extension tiers, so aggregate evidence can actually be discovered and extracted (not just consumed); symmetric modes (intercrop/strip_crop/mixed_crop/companion_crop) are canonicalized on load so one claim mirrors both ordered cells and resolves either ordering, while directional modes (rotation/relay_crop/…) stay one-directional; `build-relationship-graph` indexes evidence-bearing claims by (mode, subject_node, object_node); `resolve-crop-relationship` answers a pair from exact crop evidence, then cross-group inference (family > functional_group > genus, in direction), with host-risk caveat overlays. A routing guard blocks the same span from both the relationship and management-parameter lanes.",
                "Directional-evidence assignment from neutral unordered sources is exercised by counts only until the Opus extraction lane lands; the resolver answers one mode per call (no per-mode aggregation yet); genus-level aggregate queries and quantitative LER synthesis are out of scope.",
                "`src/crop_search_framework/relationships.py`, `src/crop_search_framework/relationship_pipeline.py`, `config/relationships/node-catalog.json`, `config/relationships/relationship-vocabulary.json`",
            ),
            capability(
                "Global tier-aware query planning",
                "operational",
                "Plans global searches across peer-reviewed science, textbook/reference, international institutions, extension/public agronomy, and industry/grower guides; `pilot-global-wheat-001` has been executed end to end.",
                "Only the wheat global benchmark has been executed so far; rice, sunflower, and tomato global runs remain pending.",
                "{0} planned global queries across {1} run configs; {2} global run executed".format(sum(global_query_counts.values()), len(global_query_counts), global_artifact_totals["executed_runs"]),
            ),
            capability(
                "Live web search",
                "partial",
                "Can call the local `search-web` tool, use source-tier-aware discovery connectors, and rank sources by crop, parameter, topic, and source-tier signals.",
                "DuckDuckGo HTML search can still return empty results or 403s; Google Books rate limiting and peer-reviewed connector precision need provider-specific tuning.",
                "`pilot-global-wheat-001`: {0} queries, {1} captured sources, {2} search failures, {3} source failures".format(
                    global_artifact_totals["queries_executed"],
                    global_artifact_totals["unique_sources_captured"],
                    global_artifact_totals["search_failures"],
                    global_artifact_totals["source_failures"],
                ),
            ),
            capability(
                "Fetch and parse",
                "partial",
                "Fetches HTML/PDF and extracts raw text, snippets, publication hints, candidate claims, and lightweight evidence-fragment labels.",
                "PDF/table parsing remains heuristic; CSV/table-heavy documents and scientific full-text structures need stronger semantic parsers.",
                "`src/crop_search_framework/dev_tools/fetch_web.py`, `src/crop_search_framework/dev_tools/parse_document.py`",
            ),
            capability(
                "Claim cleanup",
                "partial",
                "Filters many source headers, bylines, navigation fragments, table captions, and layout artifacts.",
                "Still needs semantic table extraction and better distinction between true recommendations and low-value descriptive text.",
                "`src/crop_search_framework/quality.py`; {0} normalized U.S. pilot claims after cleanup".format(artifact_totals["normalized_claims"]),
            ),
            capability(
                "Claim normalization",
                "partial",
                "Normalizes temperature, GDU, water, date-window, text, attribute subtype, provenance, confidence, and conflict status for current pilots.",
                "Unit coverage, crop-stage modeling, cultivar specificity, management recommendations, and non-temperature parameters need expansion.",
                "`src/crop_search_framework/normalize.py`",
            ),
            capability(
                "Geolocation",
                "partial",
                "Separates claim applicability from source origin; geocodes U.S. states/counties with Census records, custom regions, and verified farm points.",
                "Non-U.S. administrative geocoding and authoritative production-region polygons are not implemented yet.",
                "`src/crop_search_framework/geocoding.py`, `data/gazetteer/`",
            ),
            capability(
                "Review and durable promotion",
                "operational",
                "Reviews normalized claims, flags conflicts, promotes canonical/regional/merge candidates, and writes durable claim artifacts.",
                "Manual adjudication semantics are still basic; the expanded wheat run promoted only a small subset of normalized claims, so promotion needs stricter trait specificity and source-tier precedence.",
                "`src/crop_search_framework/review.py`, `src/crop_search_framework/promote.py`; {0} promoted U.S. pilot claims, {1} promoted global pilot claims".format(artifact_totals["promoted_claims"], global_artifact_totals["promoted_claims"]),
            ),
            capability(
                "Parameter coverage reporting",
                "operational",
                "Reports requested, normalized, promoted, missing, needs-review, and source-tier-specific parameter coverage.",
                "Coverage scoring reports tier heterogeneity but does not yet adjudicate scientific-vs-regional precedence.",
                "`src/crop_search_framework/coverage.py`, `exploration/coverage/`",
            ),
            capability(
                "PostgreSQL path",
                "partial",
                "Exports load-ready SQL and can load if `POSTGRES_DSN` is configured.",
                "No persistent staging database workflow, migrations runner, or deployment environment is established.",
                "`src/crop_search_framework/postgres_loader.py`, `data/postgres/`",
            ),
            capability(
                "Evaluation and CI",
                "partial",
                "Has unit tests for normalization/review, source tiers, geocoding, parser cleanup, and handoff rendering.",
                "No formal CI workflow, golden extraction set, precision/recall dashboard, or live-run regression suite yet.",
                "`tests/`; current test count tracked by unittest output",
            ),
            capability(
                "Scientific and textbook evidence handling",
                "partial",
                "Plans searches for peer-reviewed and textbook/reference tiers, records source-tier metadata, captures metadata-only sources, and discovers from OpenAlex, Crossref, Google Books, and Open Library.",
                "The wheat benchmark produced textbook/reference normalized claims but zero peer-reviewed normalized claims; Semantic Scholar/PubMed connectors, stronger scholarly queries, Google Books retry/backoff, and paywalled text extraction remain missing.",
                "`pilot-global-wheat-001`: {0} textbook/reference normalized claims, {1} peer-reviewed normalized claims".format(global_artifact_totals["textbook_normalized_claims"], global_artifact_totals["peer_reviewed_normalized_claims"]),
            ),
        ]

    def global_query_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for path in sorted((self.repo_root / "config" / "runs").glob("pilot-global-*.json")):
            run_config = load_json(path)
            self.registry.validate("exploration-run.schema.json", run_config)
            counts[str(path.relative_to(self.repo_root))] = len(query_plan_for_run(self.repo_root, run_config))
        return counts

    def parameter_catalog_summary(self) -> Dict[str, Any]:
        manifest = load_json(self.repo_root / "config" / "parameters" / "core-crop-parameters.json")
        self.registry.validate("parameter-manifest.schema.json", manifest)
        families = sorted({parameter["family"] for parameter in manifest.get("parameters", [])})
        return {
            "version": manifest.get("manifest_version", ""),
            "count": len(manifest.get("parameters", [])),
            "family_count": len(families),
            "families": families,
        }

    def relationship_matrix_summary(self) -> Dict[str, Any]:
        matrix = build_relationship_matrix(self.repo_root)
        self.registry.validate("crop-relationship-matrix.schema.json", matrix)
        return {
            "crop_count": matrix["crop_count"],
            "cell_count": matrix["cell_count"],
            "mode_count": len(matrix["relationship_modes"]),
        }

    def artifact_totals(self) -> Dict[str, int]:
        totals = {
            "raw_candidate_claims": 0,
            "normalized_claims": 0,
            "promoted_claims": 0,
            "manual_review_claims": 0,
        }
        for raw_summary in (self.repo_root / "exploration" / "raw").glob("pilot-us-*/summary.json"):
            totals["raw_candidate_claims"] += load_json(raw_summary).get("candidate_claim_count", 0)
        for normalized_summary in (self.repo_root / "exploration" / "normalized").glob("pilot-us-*/summary.json"):
            totals["normalized_claims"] += load_json(normalized_summary).get("normalized_claims", 0)
        for durable_claims in (self.repo_root / "memory" / "durable").glob("pilot-us-*/claims.json"):
            totals["promoted_claims"] += len(load_json(durable_claims).get("promoted_claims", []))
        for review_summary in (self.repo_root / "exploration" / "review").glob("pilot-us-*/summary.json"):
            totals["manual_review_claims"] += load_json(review_summary).get("manual_review_claims", 0)
        return totals

    def global_artifact_totals(self) -> Dict[str, int]:
        totals = {
            "executed_runs": 0,
            "queries_executed": 0,
            "unique_sources_captured": 0,
            "raw_candidate_claims": 0,
            "normalized_claims": 0,
            "promoted_claims": 0,
            "manual_review_claims": 0,
            "search_failures": 0,
            "source_failures": 0,
            "peer_reviewed_normalized_claims": 0,
            "textbook_normalized_claims": 0,
        }
        raw_summaries = list((self.repo_root / "exploration" / "raw").glob("pilot-global-*/summary.json"))
        totals["executed_runs"] = len(raw_summaries)
        for raw_summary in raw_summaries:
            raw_payload = load_json(raw_summary)
            totals["queries_executed"] += raw_payload.get("queries_executed", 0)
            totals["unique_sources_captured"] += raw_payload.get("unique_sources_captured", 0)
            totals["raw_candidate_claims"] += raw_payload.get("candidate_claim_count", 0)
            totals["search_failures"] += raw_payload.get("search_failure_count", 0)
            totals["source_failures"] += raw_payload.get("source_failure_count", 0)
        for normalized_summary in (self.repo_root / "exploration" / "normalized").glob("pilot-global-*/summary.json"):
            totals["normalized_claims"] += load_json(normalized_summary).get("normalized_claims", 0)
        for durable_claims in (self.repo_root / "memory" / "durable").glob("pilot-global-*/claims.json"):
            totals["promoted_claims"] += len(load_json(durable_claims).get("promoted_claims", []))
        for review_summary in (self.repo_root / "exploration" / "review").glob("pilot-global-*/summary.json"):
            totals["manual_review_claims"] += load_json(review_summary).get("manual_review_claims", 0)
        for coverage in (self.repo_root / "exploration" / "coverage").glob("pilot-global-*/coverage.json"):
            for tier in load_json(coverage).get("source_tier_summary", []):
                if tier.get("source_tier_id") == "peer_reviewed_science":
                    totals["peer_reviewed_normalized_claims"] += tier.get("normalized_claim_count", 0)
                if tier.get("source_tier_id") == "textbook_reference":
                    totals["textbook_normalized_claims"] += tier.get("normalized_claim_count", 0)
        return totals


def capability(area: str, status: str, can_do: str, missing: str, evidence: str) -> Dict[str, str]:
    return {
        "area": area,
        "status": status,
        "can_do": can_do,
        "missing": missing,
        "evidence": evidence,
    }


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def current_time() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
