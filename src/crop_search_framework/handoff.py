from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .parameters import query_plan_for_run
from .schema_registry import SchemaRegistry


DEFAULT_RUN_ORDER = [
    "pilot-us-corn-iowa-001",
    "pilot-us-wheat-001",
    "pilot-us-rice-001",
    "pilot-us-sunflower-001",
    "pilot-us-tomato-001",
    "pilot-global-wheat-001",
]


class HandoffWriter:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.registry = SchemaRegistry(repo_root)

    def write(self, output_path: Path) -> Dict[str, Any]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        content = self.render()
        output_path.write_text(content, encoding="utf-8")
        return {
            "handoff_path": str(output_path.relative_to(self.repo_root)),
            "generated_at": current_time(),
        }

    def render(self) -> str:
        generated_at = current_time()
        global_runs = self.global_run_records()
        artifact_rows = self.artifact_rows(DEFAULT_RUN_ORDER)
        global_wheat = self.global_benchmark_summary("pilot-global-wheat-001")
        parameter_catalog = self.parameter_catalog_summary()
        recent_log = latest_log_heading(self.repo_root / "docs" / "IMPLEMENTATION_LOG.md")
        git_note = "available" if (self.repo_root / ".git").exists() else "not initialized in this workspace"
        wheat_query_count = next((record["query_count"] for record in global_runs if record["run_id"] == "pilot-global-wheat-001"), 0)

        current_state_lines = [
            "- The local pipeline can run search, fetch, parse, normalize, review, promote, coverage, and PostgreSQL SQL export.",
            "- The current production direction is global and source-tier-aware, not U.S.-only.",
            "- The core parameter manifest now contains {0} crop physiology, phenology, canopy, root, water, soil, nutrient, stress, establishment, quality, harvest, and management parameters across {1} families.".format(
                parameter_catalog["count"], parameter_catalog["family_count"]
            ),
            "- Source tiers are implemented for peer-reviewed science, open textbook/reference material, international institutions, extension/public agronomy publications, and industry/grower guides.",
            "- Peer-reviewed and textbook/reference discovery uses OpenAlex, Crossref, Google Books, and Open Library connectors before DuckDuckGo fallback, with metadata-only capture for inaccessible papers/books.",
            "- U.S. state/county geocoding uses 2025 Census Gazetteer records; named production regions remain explicit custom approximate records.",
            "- Existing U.S. pilot artifacts are retained as fixture-like evidence for parser, normalization, review, promotion, coverage, geolocation, and SQL export behavior.",
        ]
        if global_wheat:
            current_state_lines.extend(
                [
                    "- `pilot-global-wheat-001` has been run end to end: {queries} live queries, {sources} captured sources, {candidates} candidate claims, {normalized} normalized claims, {promoted} promoted claims, and SQL export generated.".format(
                        **global_wheat
                    ),
                    benchmark_alignment_line(global_wheat["queries"], wheat_query_count),
                    "- The global wheat tier metrics moved beyond extension-heavy evidence by normalized-claim count: textbook/reference {textbook_normalized}, international institutions {international_normalized}, industry/grower {industry_normalized}, extension {extension_normalized}, peer-reviewed {peer_reviewed_normalized}.".format(
                        **global_wheat
                    ),
                    "- Peer-reviewed discovery is still metadata-heavy: {peer_reviewed_captured} peer-reviewed captures produced {peer_reviewed_normalized} normalized claims, so scholarly full-text retrieval and query precision remain the main evidence gap.".format(
                        **global_wheat
                    ),
                ]
            )
        else:
            current_state_lines.append("- Global tier-aware run configs are ready, but no global run has been executed live end to end yet.")

        if global_wheat:
            next_step_lines = [
                "Use `pilot-global-wheat-001` as the benchmark for the next implementation pass: improve extraction/normalization for the new trait families, tighten peer-reviewed discovery/query terms, add provider-specific book connector retry/backoff, improve PDF/book parsing, and make promotion reject broad non-trait descriptive claims.",
                "",
                "After those fixes, rerun the wheat benchmark as a regression check:",
                "",
                "```bash",
                "PYTHONPATH=src python3 -m crop_search_framework.cli run-exploration --run-config config/runs/pilot-global-wheat.json",
                "PYTHONPATH=src python3 -m crop_search_framework.cli normalize-run pilot-global-wheat-001",
                "PYTHONPATH=src python3 -m crop_search_framework.cli review-run pilot-global-wheat-001",
                "PYTHONPATH=src python3 -m crop_search_framework.cli promote-run pilot-global-wheat-001",
                "PYTHONPATH=src python3 -m crop_search_framework.cli coverage-run pilot-global-wheat-001",
                "PYTHONPATH=src python3 -m crop_search_framework.cli load-postgres pilot-global-wheat-001",
                "PYTHONPATH=src python3 -m crop_search_framework.cli write-capability-map",
                "PYTHONPATH=src python3 -m crop_search_framework.cli write-handoff",
                "```",
            ]
        else:
            next_step_lines = [
                "Run a global, tier-aware live exploration without U.S.-centric seed fallback:",
                "",
                "```bash",
                "PYTHONPATH=src python3 -m crop_search_framework.cli run-exploration --run-config config/runs/pilot-global-wheat.json",
                "```",
                "",
                "Then continue the pipeline:",
                "",
                "```bash",
                "PYTHONPATH=src python3 -m crop_search_framework.cli normalize-run pilot-global-wheat-001",
                "PYTHONPATH=src python3 -m crop_search_framework.cli review-run pilot-global-wheat-001",
                "PYTHONPATH=src python3 -m crop_search_framework.cli promote-run pilot-global-wheat-001",
                "PYTHONPATH=src python3 -m crop_search_framework.cli coverage-run pilot-global-wheat-001",
                "PYTHONPATH=src python3 -m crop_search_framework.cli load-postgres pilot-global-wheat-001",
                "PYTHONPATH=src python3 -m crop_search_framework.cli write-capability-map",
                "PYTHONPATH=src python3 -m crop_search_framework.cli write-handoff",
                "```",
            ]

        known_constraints = [
            "- Paywalled scientific papers and books may be used for metadata discovery only unless accessible full text is available.",
            "- County extraction is implemented and test-covered, but current pilot artifacts do not yet contain state-qualified county claims after cleanup.",
            "- SQL export exists and local/disposable PostgreSQL has been tested earlier, but a persistent staging database workflow is still not established.",
            "- This workspace git status is `{0}`; docs and generated artifacts are the continuity source unless a repo is initialized.".format(git_note),
        ]
        if global_wheat:
            known_constraints = [
                "- Live global execution has only been benchmarked for wheat; rice, sunflower, and tomato global configs are still pending.",
                "- The comprehensive wheat run is much larger than the original 60-query pilot, so future live execution should use batching/rate-limit controls and progress logging.",
                "- Google Books was rate-limited during the wheat benchmark, so textbook/reference normalized claims came from DuckDuckGo-discovered open sources rather than book APIs.",
                "- The peer-reviewed tier captured mostly Crossref metadata-only records and produced zero normalized trait claims in the wheat benchmark.",
                "- The expanded run queried 85 parameters, but current normalization/review rules only produced normalized claims for a small subset of requested parameters.",
                *known_constraints,
            ]
        else:
            known_constraints.insert(0, "- Live discovery has provider connectors plus DuckDuckGo fallback, but connector yield still needs a fresh global live-run benchmark.")
            known_constraints.insert(2, "- Existing raw captures are still U.S.-heavy; the global run configs have not been executed yet.")

        lines = [
            "# Project Handoff",
            "",
            "Generated at: `{0}`".format(generated_at),
            "",
            "This is the canonical start-here file for future sessions. Read this first, then use the referenced files only as needed.",
            "",
            "## Current Goal",
            "",
            "Build a provenance-aware crop search runner that discovers crop physiological parameters and management recommendations from legally accessible sources, normalizes them into structured claims, preserves source/claim geolocation, reviews them for promotion, and exports load-ready records.",
            "",
            "## Current State",
            "",
            *current_state_lines,
            "",
            "## Start Here",
            "",
            "1. Read this file.",
            "2. Read `docs/CAPABILITY_MAP.md` for the current can/cannot-do inventory.",
            "3. Read `README.md` for command overview.",
            "4. Read `docs/IMPLEMENTATION_LOG.md` only if you need historical detail.",
            "5. Use `config/runs/pilot-global-wheat.json` as the next-run template unless the user asks for a different crop or geography.",
            "",
            "## Next Recommended Step",
            "",
            *next_step_lines,
            "",
            "## Global Run Configs",
            "",
            "| Run config | Run ID | Query count | Notes |",
            "| --- | --- | ---: | --- |",
        ]
        for record in global_runs:
            lines.append(
                "| `{path}` | `{run_id}` | {query_count} | {notes} |".format(**record)
            )

        lines.extend(
            [
                "",
                "## Existing Artifact Snapshot",
                "",
                "| Run ID | Raw candidate claims | Normalized | Manual review | Rejected | Promoted | Coverage |",
                "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for row in artifact_rows:
            lines.append(
                "| `{run_id}` | {candidate_claims} | {normalized_claims} | {manual_review_claims} | {rejected_claims} | {promoted_claims} | {coverage_summary} |".format(
                    **row
                )
            )

        lines.extend(
            [
                "",
                "## Key Files",
                "",
                "- `config/source-tiers/default.json`: source-tier policy for comprehensive accessible evidence.",
                "- `config/runs/pilot-global-*.json`: global, tier-aware run configs for upcoming searches.",
                "- `src/crop_search_framework/parameters.py`: parameter and source-tier query planning.",
                "- `src/crop_search_framework/source_tiers.py`: source-tier manifest loading and scoring signals.",
                "- `src/crop_search_framework/dev_tools/discovery_connectors.py`: OpenAlex, Crossref, Google Books, and Open Library discovery adapters.",
                "- `src/crop_search_framework/coverage.py`: parameter and source-tier coverage reporting.",
                "- `src/crop_search_framework/geocoding.py`: Census-backed state/county geocoding plus custom production regions.",
                "- `src/crop_search_framework/normalize.py`: claim normalization, geolocation inference, conflict grouping.",
                "- `docs/CAPABILITY_MAP.md`: generated inventory of operational, configured, partial, and missing pipeline capabilities.",
                "- `docs/IMPLEMENTATION_LOG.md`: chronological track record.",
                "- `docs/ROADMAP.md`: remaining implementation phases.",
                "",
                "## Known Constraints",
                "",
                *known_constraints,
                "",
                "## Validation Baseline",
                "",
                "Use these commands after implementation work:",
                "",
                "```bash",
                "PYTHONPATH=src python3 -m unittest discover -s tests",
                "PYTHONPATH=src python3 -m compileall src tests",
                "PYTHONPATH=src python3 -m crop_search_framework.cli write-capability-map",
                "PYTHONPATH=src python3 -m crop_search_framework.cli write-handoff",
                "```",
                "",
                "Last implementation-log section detected: `{0}`.".format(recent_log or "unknown"),
                "",
                "## Automatic Session Rule",
                "",
                "Future sessions should read `AGENTS.md`, this file, and `docs/CAPABILITY_MAP.md` before making changes. Before ending a session that changes code, configs, generated artifacts, docs, or pipeline capabilities, run `PYTHONPATH=src python3 -m crop_search_framework.cli write-capability-map` and `PYTHONPATH=src python3 -m crop_search_framework.cli write-handoff`; update `docs/IMPLEMENTATION_LOG.md` for meaningful milestones.",
                "",
            ]
        )
        return "\n".join(lines)

    def global_run_records(self) -> List[Dict[str, Any]]:
        records = []
        for path in sorted((self.repo_root / "config" / "runs").glob("pilot-global-*.json")):
            run_config = load_json(path)
            self.registry.validate("exploration-run.schema.json", run_config)
            query_count = len(query_plan_for_run(self.repo_root, run_config))
            notes = []
            if run_config.get("region_scope", {}).get("level") == "global":
                notes.append("global scope")
            if run_config.get("source_tier_policy_path"):
                notes.append("source-tier policy")
            if not run_config.get("use_source_seeds", True):
                notes.append("no seed fallback")
            if (self.repo_root / "exploration" / "raw" / run_config["run_id"] / "summary.json").exists():
                notes.append("executed live")
            records.append(
                {
                    "path": str(path.relative_to(self.repo_root)),
                    "run_id": run_config["run_id"],
                    "query_count": query_count,
                    "notes": ", ".join(notes) or "standard run",
                }
            )
        return records

    def global_benchmark_summary(self, run_id: str) -> Optional[Dict[str, Any]]:
        raw_summary = load_optional_json(self.repo_root / "exploration" / "raw" / run_id / "summary.json")
        if not raw_summary:
            return None
        normalized_summary = load_optional_json(self.repo_root / "exploration" / "normalized" / run_id / "summary.json")
        review_summary = load_optional_json(self.repo_root / "exploration" / "review" / run_id / "summary.json")
        durable_report = load_optional_json(self.repo_root / "memory" / "durable" / run_id / "claims.json")
        coverage = load_optional_json(self.repo_root / "exploration" / "coverage" / run_id / "coverage.json")
        tier_counts = {
            "extension_normalized": 0,
            "industry_normalized": 0,
            "international_normalized": 0,
            "peer_reviewed_normalized": 0,
            "peer_reviewed_captured": 0,
            "textbook_normalized": 0,
        }
        for tier in coverage.get("source_tier_summary", []):
            tier_id = tier.get("source_tier_id")
            if tier_id == "extension_publication":
                tier_counts["extension_normalized"] = tier.get("normalized_claim_count", 0)
            if tier_id == "industry_grower_guide":
                tier_counts["industry_normalized"] = tier.get("normalized_claim_count", 0)
            if tier_id == "international_institution":
                tier_counts["international_normalized"] = tier.get("normalized_claim_count", 0)
            if tier_id == "peer_reviewed_science":
                tier_counts["peer_reviewed_normalized"] = tier.get("normalized_claim_count", 0)
                tier_counts["peer_reviewed_captured"] = tier.get("captured_source_count", 0)
            if tier_id == "textbook_reference":
                tier_counts["textbook_normalized"] = tier.get("normalized_claim_count", 0)
        return {
            "queries": raw_summary.get("queries_executed", 0),
            "sources": raw_summary.get("unique_sources_captured", 0),
            "candidates": raw_summary.get("candidate_claim_count", 0),
            "normalized": normalized_summary.get("normalized_claims", 0),
            "manual_review": review_summary.get("manual_review_claims", 0),
            "promoted": len(durable_report.get("promoted_claims", [])),
            **tier_counts,
        }

    def parameter_catalog_summary(self) -> Dict[str, Any]:
        manifest = load_json(self.repo_root / "config" / "parameters" / "core-crop-parameters.json")
        self.registry.validate("parameter-manifest.schema.json", manifest)
        families = sorted({parameter["family"] for parameter in manifest.get("parameters", [])})
        return {
            "count": len(manifest.get("parameters", [])),
            "family_count": len(families),
            "families": families,
        }

    def artifact_rows(self, run_ids: Iterable[str]) -> List[Dict[str, Any]]:
        return [self.artifact_row(run_id) for run_id in run_ids]

    def artifact_row(self, run_id: str) -> Dict[str, Any]:
        raw_summary = load_optional_json(self.repo_root / "exploration" / "raw" / run_id / "summary.json")
        normalized_summary = load_optional_json(self.repo_root / "exploration" / "normalized" / run_id / "summary.json")
        review_summary = load_optional_json(self.repo_root / "exploration" / "review" / run_id / "summary.json")
        durable_report = load_optional_json(self.repo_root / "memory" / "durable" / run_id / "claims.json")
        coverage_summary = load_optional_json(self.repo_root / "exploration" / "coverage" / run_id / "summary.json")
        return {
            "run_id": run_id,
            "candidate_claims": raw_summary.get("candidate_claim_count", 0),
            "normalized_claims": normalized_summary.get("normalized_claims", 0),
            "manual_review_claims": review_summary.get("manual_review_claims", 0),
            "rejected_claims": review_summary.get("rejected_claims", 0),
            "promoted_claims": len(durable_report.get("promoted_claims", [])),
            "coverage_summary": render_coverage(coverage_summary),
        }


def render_coverage(summary: Dict[str, Any]) -> str:
    if not summary:
        return "not generated"
    return "{0} normalized / {1} promoted / {2} missing".format(
        summary.get("parameters_with_normalized_claims", 0),
        summary.get("parameters_with_promoted_claims", 0),
        summary.get("parameters_missing", 0),
    )


def benchmark_alignment_line(executed_queries: int, planned_queries: int) -> str:
    if executed_queries == planned_queries:
        return "- Existing `pilot-global-wheat-001` artifacts match the current comprehensive wheat config: {0} planned queries and {1} executed queries.".format(
            planned_queries,
            executed_queries,
        )
    return "- Existing `pilot-global-wheat-001` artifacts do not match the current wheat config: {0} planned queries and {1} executed queries.".format(
        planned_queries,
        executed_queries,
    )


def latest_log_heading(path: Path) -> str:
    if not path.exists():
        return ""
    headings = [
        line.strip("# ").strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    ]
    return headings[-1] if headings else ""


def load_optional_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def current_time() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
