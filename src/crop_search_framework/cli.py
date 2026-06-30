from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from .backfill import backfill_corpus
from .capabilities import CapabilityMapWriter
from .corpus import build_corpus, corpus_qa
from .discovery import discover
from .fetch_selection import select_fetch_queue
from .fetch_stage import run_fetch
from .seeds import validate_seeds
from .coverage import ParameterCoverageRunner
from .vault_render import render_vault
from .exploration import ExplorationRunner
from .handoff import HandoffWriter
from .hooks import HookRunner
from .llm_extract import extract_run, make_backend
from .normalize import NormalizationRunner
from .postgres_loader import PostgresLoader
from .eval_harness import eval_extraction, eval_retrieval
from .promote import PromotionRunner
from .parameters import query_plan_for_run
from .relationships import (
    DEFAULT_AGGREGATE_NODE_TYPES,
    DEFAULT_CROP_DIR,
    DEFAULT_SOURCE_TIER_POLICY_PATH,
    DEFAULT_VOCABULARY_PATH,
    build_relationship_query_plan,
    discover_relationships,
    write_relationship_matrix,
    write_relationship_query_plan,
)
from .relationship_pipeline import (
    build_merged_relationship_graph,
    build_relationship_corpus,
    build_relationship_graph,
    eval_relationships,
    fetch_crop_references,
    fetch_relationships,
    populate_relationship_matrix,
    relationship_coverage_report,
    resolve_crop_relationship,
    select_relationship_fetch,
    validate_relationship_claims,
)
from .review import ClaimReviewRunner
from .schema_registry import SchemaRegistry


DEFAULT_VAULT = (
    "/Users/admin/Library/Mobile Documents/iCloud~md~obsidian/Documents/Tino_deCrops"
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else repo_root() / path


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_command(args: argparse.Namespace) -> int:
    registry = SchemaRegistry(repo_root())
    payload = load_json(resolve_path(args.payload))
    registry.validate(args.schema, payload)
    print(f"valid: {args.payload} matches {args.schema}")
    return 0


def validate_manifest_command(args: argparse.Namespace) -> int:
    registry = SchemaRegistry(repo_root())
    manifest_path = resolve_path(args.manifest)
    payload = load_json(manifest_path)
    registry.validate("mcp-server-manifest.schema.json", payload)
    print(f"valid: {manifest_path} is a valid MCP server manifest")
    return 0


def emit_hook_command(args: argparse.Namespace) -> int:
    event = load_json(resolve_path(args.payload))
    if event["event_name"] != args.event_name:
        raise ValueError(
            f"Event name mismatch: CLI received {args.event_name}, payload contains {event['event_name']}"
        )
    config_path = resolve_path(args.config)
    runner = HookRunner(repo_root(), config_path)
    results = runner.run_event(event)
    for result in results:
        print(result)
    return 0


def run_exploration_command(args: argparse.Namespace) -> int:
    runner = ExplorationRunner(
        repo_root=repo_root(),
        run_config_path=resolve_path(args.run_config),
        manifest_path=resolve_path(args.manifest),
        hook_config_path=resolve_path(args.config),
    )
    summary = runner.execute()
    print(json.dumps(summary, indent=2))
    return 0


def discover_command(args: argparse.Namespace) -> int:
    summary = discover(repo_root(), resolve_path(args.run_config), resume=args.resume)
    print(json.dumps(summary, indent=2))
    return 0


def fetch_command(args: argparse.Namespace) -> int:
    summary = run_fetch(
        repo_root(), resolve_path(args.run_config), resume=args.resume, limit=args.limit
    )
    print(json.dumps(summary, indent=2))
    return 0


def select_fetch_command(args: argparse.Namespace) -> int:
    summary = select_fetch_queue(
        repo_root(),
        args.run_id,
        policy_path=args.policy,
        resolve_oa=args.resolve_oa,
        email=args.email,
    )
    print(json.dumps(summary, indent=2))
    return 0


def seeds_validate_command(args: argparse.Namespace) -> int:
    report = validate_seeds(
        repo_root(), args.registry, args.manifest, check_links=args.check_links
    )
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


def plan_queries_command(args: argparse.Namespace) -> int:
    run_config = load_json(resolve_path(args.run_config))
    SchemaRegistry(repo_root()).validate("exploration-run.schema.json", run_config)
    query_plan = query_plan_for_run(repo_root(), run_config)
    print(
        json.dumps(
            {
                "run_id": run_config["run_id"],
                "query_count": len(query_plan),
                "queries": [
                    {
                        "query": item.query,
                        "parameter_id": item.parameter_id,
                        "parameter_family": item.parameter_family,
                        "parameter_label": item.parameter_label,
                        "source_tier_id": item.source_tier_id,
                        "source_tier_label": item.source_tier_label,
                    }
                    for item in query_plan
                ],
            },
            indent=2,
        )
    )
    return 0


def write_relationship_matrix_command(args: argparse.Namespace) -> int:
    summary = write_relationship_matrix(
        repo_root(),
        resolve_path(args.output),
        crop_dir=args.crop_dir,
        vocabulary_path=args.vocabulary,
        mode_ids=args.mode,
        include_self_pairs=not args.exclude_self_pairs,
    )
    print(json.dumps(summary, indent=2))
    return 0


def plan_relationship_queries_command(args: argparse.Namespace) -> int:
    kwargs = {
        "crop_dir": args.crop_dir,
        "vocabulary_path": args.vocabulary,
        "mode_ids": args.mode,
        "source_tier_policy_path": args.source_tier_policy_path,
        "source_tier_policy_id": args.source_tier_policy_id,
        "source_tier_ids": args.source_tier_id,
        "queries_per_pair": args.queries_per_pair,
        "query_terms_per_source_tier": args.query_terms_per_source_tier,
        "max_pairs": args.max_pairs,
        "region_name": args.region,
        "include_self_pairs": not args.exclude_self_pairs,
        "pair_mode": args.pair_mode,
        "node_mode": args.node_mode,
        "aggregate_node_types": args.aggregate_node_type or DEFAULT_AGGREGATE_NODE_TYPES,
    }
    if args.output:
        summary = write_relationship_query_plan(repo_root(), resolve_path(args.output), **kwargs)
        print(json.dumps(summary, indent=2))
        return 0
    plan = build_relationship_query_plan(repo_root(), **kwargs)
    print(json.dumps(plan, indent=2))
    return 0


def discover_relationships_command(args: argparse.Namespace) -> int:
    summary = discover_relationships(
        repo_root(),
        args.run_id,
        crop_dir=args.crop_dir,
        vocabulary_path=args.vocabulary,
        mode_ids=args.mode,
        source_tier_policy_path=args.source_tier_policy_path,
        source_tier_policy_id=args.source_tier_policy_id,
        source_tier_ids=args.source_tier_id,
        queries_per_pair=args.queries_per_pair,
        query_terms_per_source_tier=args.query_terms_per_source_tier,
        max_pairs=args.max_pairs,
        max_results_per_query=args.max_results_per_query,
        region_name=args.region,
        include_self_pairs=not args.exclude_self_pairs,
        limit_queries=args.limit_queries,
        pair_mode=args.pair_mode,
        node_mode=args.node_mode,
        aggregate_node_types=args.aggregate_node_type or DEFAULT_AGGREGATE_NODE_TYPES,
    )
    print(json.dumps(summary, indent=2))
    return 0


def build_relationship_graph_command(args: argparse.Namespace) -> int:
    graph = build_relationship_graph(repo_root(), args.run_id)
    summary = {
        "run_id": graph["run_id"],
        "generated_at": graph["generated_at"],
        "claim_count": graph["claim_count"],
        "direct_keys": len(graph["direct"]),
        "aggregate_keys": len(graph["aggregate"]),
        "host_overlay_keys": len(graph["host_overlays"]),
    }
    print(json.dumps(summary, indent=2))
    return 0


def resolve_crop_relationship_command(args: argparse.Namespace) -> int:
    resolved = resolve_crop_relationship(
        repo_root(), args.run_id, args.subject, args.object, mode=args.mode,
    )
    print(json.dumps(resolved, indent=2))
    return 0


def relationship_coverage_report_command(args: argparse.Namespace) -> int:
    report = relationship_coverage_report(
        repo_root(),
        args.run_id,
        modes=tuple(args.mode) if args.mode else ("rotation", "intercrop"),
        crop_dir=args.crop_dir,
        persist_label=args.label,
    )
    print(json.dumps(report, indent=2))
    return 0


def normalize_units_command(args: argparse.Namespace) -> int:
    from .unit_normalize import normalize_units_run
    print(json.dumps(normalize_units_run(repo_root(), args.run_id, claims_subdir=args.claims_subdir, output_subdir=args.output_subdir), indent=2))
    return 0


def render_calibration_command(args: argparse.Namespace) -> int:
    from .calibration import render_local_calibration
    summary = render_local_calibration(
        repo_root(), args.run_id, crop=args.crop, region=args.region,
        vault_path=Path(args.vault).expanduser(), claims_subdir=args.claims_subdir, dry_run=args.dry_run,
    )
    if args.dry_run:
        print(summary.pop("preview", ""))
    print(json.dumps(summary, indent=2))
    return 0


def select_relationship_fetch_command(args: argparse.Namespace) -> int:
    print(json.dumps(select_relationship_fetch(repo_root(), args.run_id, policy_path=args.policy), indent=2))
    return 0


def fetch_relationships_command(args: argparse.Namespace) -> int:
    print(json.dumps(fetch_relationships(repo_root(), args.run_id, resume=args.resume, limit=args.limit, crop=args.crop), indent=2))
    return 0


def build_relationship_corpus_command(args: argparse.Namespace) -> int:
    print(json.dumps(build_relationship_corpus(repo_root(), args.run_id), indent=2))
    return 0


def fetch_crop_references_command(args: argparse.Namespace) -> int:
    print(json.dumps(fetch_crop_references(
        repo_root(), args.run_id, crop_dir=args.crop_dir, limit=args.limit, crop_ids=args.crop,
    ), indent=2))
    return 0


def populate_relationship_matrix_command(args: argparse.Namespace) -> int:
    print(json.dumps(populate_relationship_matrix(repo_root(), args.run_id, mode_ids=args.mode), indent=2))
    return 0


def validate_relationship_claims_command(args: argparse.Namespace) -> int:
    report = validate_relationship_claims(repo_root(), args.run_id)
    print(json.dumps({k: v for k, v in report.items() if k != "claims"}, indent=2))
    return 0 if not report["invalid"] else 1


def eval_relationships_command(args: argparse.Namespace) -> int:
    print(json.dumps(eval_relationships(repo_root(), args.run_id), indent=2))
    return 0


def extract_run_command(args: argparse.Namespace) -> int:
    cache_dir = repo_root() / "exploration" / "llm_cache" / args.run_id
    backend = make_backend(
        args.backend, cache_dir=cache_dir, model=args.model, host=getattr(args, "host", None)
    )
    summary = extract_run(repo_root(), args.run_id, backend, manifest_path=args.manifest)
    print(json.dumps(summary, indent=2))
    return 0


def normalize_run_command(args: argparse.Namespace) -> int:
    runner = NormalizationRunner(
        repo_root=repo_root(),
        hook_config_path=resolve_path(args.config),
    )
    if getattr(args, "from_llm", False):
        cache_dir = repo_root() / "exploration" / "llm_cache" / args.run_id
        backend = make_backend(
            args.backend, cache_dir=cache_dir, model=args.model, host=getattr(args, "host", None)
        )
        summary = runner.normalize_run_from_llm(
            args.run_id, backend, output_subdir=args.output_subdir
        )
    else:
        summary = runner.normalize_run(args.run_id)
    print(json.dumps(summary, indent=2))
    return 0


def build_corpus_command(args: argparse.Namespace) -> int:
    summary = build_corpus(repo_root(), args.run_id, source_run=args.source_run)
    print(json.dumps(summary, indent=2))
    return 0


def corpus_qa_command(args: argparse.Namespace) -> int:
    report = corpus_qa(repo_root(), args.run_id)
    print(json.dumps(report, indent=2))
    return 0


def backfill_corpus_command(args: argparse.Namespace) -> int:
    summary = backfill_corpus(repo_root(), args.run_id, email=args.email, limit=args.limit)
    print(json.dumps(summary, indent=2))
    return 0


def render_vault_command(args: argparse.Namespace) -> int:
    from datetime import date

    summary = render_vault(
        repo_root(),
        args.run_id,
        vault_path=Path(args.vault).expanduser(),
        subdir=args.subdir,
        claims_subdir=args.claims_subdir,
        dry_run=args.dry_run,
        prune=args.prune,
        generated_at=date.today().isoformat(),
    )
    print(json.dumps(summary, indent=2))
    return 0


def load_postgres_command(args: argparse.Namespace) -> int:
    loader = PostgresLoader(repo_root())
    summary = loader.load_if_configured(args.run_id)
    print(json.dumps(summary, indent=2))
    return 0


def review_run_command(args: argparse.Namespace) -> int:
    runner = ClaimReviewRunner(repo_root())
    summary = runner.review_run(args.run_id)
    print(json.dumps(summary, indent=2))
    return 0


def promote_run_command(args: argparse.Namespace) -> int:
    runner = PromotionRunner(repo_root())
    summary = runner.promote_run(args.run_id)
    print(json.dumps(summary, indent=2))
    return 0


def coverage_run_command(args: argparse.Namespace) -> int:
    runner = ParameterCoverageRunner(repo_root())
    summary = runner.coverage_run(args.run_id)
    print(json.dumps(summary, indent=2))
    return 0


def eval_extraction_command(args: argparse.Namespace) -> int:
    gold = resolve_path(args.gold_dir) if args.gold_dir else None
    report = eval_extraction(repo_root(), args.run_id, gold_dir=gold)
    print(json.dumps(report, indent=2))
    return 0


def eval_retrieval_command(args: argparse.Namespace) -> int:
    gold = resolve_path(args.gold_dir) if args.gold_dir else None
    report = eval_retrieval(repo_root(), args.run_id, gold_dir=gold)
    print(json.dumps(report, indent=2))
    return 0


def write_handoff_command(args: argparse.Namespace) -> int:
    writer = HandoffWriter(repo_root())
    summary = writer.write(resolve_path(args.output))
    print(json.dumps(summary, indent=2))
    return 0


def write_capability_map_command(args: argparse.Namespace) -> int:
    writer = CapabilityMapWriter(repo_root())
    summary = writer.write(resolve_path(args.output))
    print(json.dumps(summary, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Crop search foundation CLI")
    subparsers = cli.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate a JSON file against a schema")
    validate.add_argument("schema", help="Schema name or path")
    validate.add_argument("payload", help="JSON payload path")
    validate.set_defaults(func=validate_command)

    manifest = subparsers.add_parser("validate-mcp", help="Validate MCP server manifest")
    manifest.add_argument(
        "--manifest",
        default="config/mcp/servers.example.json",
        help="Manifest path",
    )
    manifest.set_defaults(func=validate_manifest_command)

    emit = subparsers.add_parser("emit-hook", help="Run the configured hook chain for an event")
    emit.add_argument("event_name", help="Event name for readability")
    emit.add_argument("--payload", required=True, help="Hook event JSON file")
    emit.add_argument(
        "--config",
        default="config/hooks/default.json",
        help="Hook config path",
    )
    emit.set_defaults(func=emit_hook_command)

    exploration = subparsers.add_parser(
        "run-exploration",
        help="[DEPRECATED] Fused search+fetch+parse run. Superseded by: discover -> select-fetch -> build-corpus",
    )
    exploration.add_argument(
        "--run-config",
        default="config/runs/pilot-us-corn-iowa.json",
        help="Exploration run config path",
    )
    exploration.add_argument(
        "--manifest",
        default="config/mcp/servers.local.json",
        help="Tool manifest path",
    )
    exploration.add_argument(
        "--config",
        default="config/hooks/default.json",
        help="Hook config path",
    )
    exploration.set_defaults(func=run_exploration_command)

    discover_parser = subparsers.add_parser(
        "discover",
        help="Discovery stage: over-collect provider results into a complete ledger (no fetch)",
    )
    discover_parser.add_argument(
        "--run-config",
        default="config/runs/pilot-global-wheat.json",
        help="Exploration run config path",
    )
    discover_parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse the provider cache and re-attempt previously failed calls",
    )
    discover_parser.set_defaults(func=discover_command)

    select_fetch_parser = subparsers.add_parser(
        "select-fetch",
        help="Fetch selection: balance the discovery ledger into a fetch queue (WS-2/WS-4)",
    )
    select_fetch_parser.add_argument("run_id", help="Discovery run identifier")
    select_fetch_parser.add_argument(
        "--policy", default="config/fetch-policy/default.json", help="Fetch policy path"
    )
    select_fetch_parser.add_argument(
        "--resolve-oa", action="store_true", help="Resolve OA full-text URLs for selected scholarly candidates"
    )
    select_fetch_parser.add_argument("--email", default="", help="Contact email for the Unpaywall/Crossref polite pool")
    select_fetch_parser.set_defaults(func=select_fetch_command)

    fetch_parser = subparsers.add_parser(
        "fetch",
        help="Fetch executor: fetch+parse the selected fetch queue into raw captures (WS-2/WS-3)",
    )
    fetch_parser.add_argument(
        "--run-config", default="config/runs/pilot-global-wheat.json", help="Exploration run config path"
    )
    fetch_parser.add_argument(
        "--resume", action="store_true", help="Reuse the HTTP cache and skip captures already written"
    )
    fetch_parser.add_argument("--limit", type=int, default=None, help="Cap selected rows fetched (smoke tests)")
    fetch_parser.set_defaults(func=fetch_command)

    seeds_validate_parser = subparsers.add_parser(
        "seeds-validate",
        help="Validate a curated seed registry (schema, parameter ids, optional dead links)",
    )
    seeds_validate_parser.add_argument(
        "--registry", default="config/seeds/wheat.json", help="Seed registry path"
    )
    seeds_validate_parser.add_argument(
        "--manifest",
        default="config/parameters/core-crop-parameters.json",
        help="Parameter manifest path",
    )
    seeds_validate_parser.add_argument(
        "--check-links", action="store_true", help="Probe each seed URL for dead links"
    )
    seeds_validate_parser.set_defaults(func=seeds_validate_command)

    plan_queries = subparsers.add_parser(
        "plan-queries",
        help="Render manifest-driven search queries for a run without fetching sources",
    )
    plan_queries.add_argument(
        "--run-config",
        default="config/runs/pilot-us-corn-iowa.json",
        help="Exploration run config path",
    )
    plan_queries.set_defaults(func=plan_queries_command)

    relationship_matrix = subparsers.add_parser(
        "write-relationship-matrix",
        help="Write the dense crop_id x crop_id relationship matrix skeleton",
    )
    relationship_matrix.add_argument(
        "--output",
        default="exploration/relationships/matrix/current.json",
        help="Relationship matrix output path",
    )
    relationship_matrix.add_argument(
        "--crop-dir",
        default=DEFAULT_CROP_DIR,
        help="Crop profile directory",
    )
    relationship_matrix.add_argument(
        "--vocabulary",
        default=DEFAULT_VOCABULARY_PATH,
        help="Relationship vocabulary path",
    )
    relationship_matrix.add_argument(
        "--mode",
        action="append",
        default=None,
        help="Relationship mode to include; repeatable. Defaults to all vocabulary modes.",
    )
    relationship_matrix.add_argument(
        "--exclude-self-pairs",
        action="store_true",
        help="Omit same-crop cells; default keeps them for continuous cropping.",
    )
    relationship_matrix.set_defaults(func=write_relationship_matrix_command)

    relationship_queries = subparsers.add_parser(
        "plan-relationship-queries",
        help="Render source-tier-aware crop relationship queries without fetching sources",
    )
    relationship_queries.add_argument(
        "--output",
        default="",
        help="Optional query-plan output path. If omitted, prints the full plan.",
    )
    relationship_queries.add_argument(
        "--crop-dir",
        default=DEFAULT_CROP_DIR,
        help="Crop profile directory",
    )
    relationship_queries.add_argument(
        "--vocabulary",
        default=DEFAULT_VOCABULARY_PATH,
        help="Relationship vocabulary path",
    )
    relationship_queries.add_argument(
        "--mode",
        action="append",
        default=None,
        help="Relationship mode to query; repeatable. Defaults to the vocabulary default modes.",
    )
    relationship_queries.add_argument(
        "--source-tier-policy-path",
        default=DEFAULT_SOURCE_TIER_POLICY_PATH,
        help="Source-tier policy path",
    )
    relationship_queries.add_argument(
        "--source-tier-policy-id",
        default="",
        help="Source-tier policy id; defaults to the policy manifest default.",
    )
    relationship_queries.add_argument(
        "--source-tier-id",
        action="append",
        default=None,
        help="Specific source tier id to use; repeatable.",
    )
    relationship_queries.add_argument(
        "--queries-per-pair",
        type=int,
        default=3,
        help="Max templates per crop pair and relationship mode.",
    )
    relationship_queries.add_argument(
        "--query-terms-per-source-tier",
        type=int,
        default=3,
        help="Source-tier vocabulary terms appended to each query.",
    )
    relationship_queries.add_argument(
        "--max-pairs",
        type=int,
        default=None,
        help="Optional deterministic cap for future larger crop universes.",
    )
    relationship_queries.add_argument(
        "--region",
        default="global",
        help="Region term to append; 'global' appends no region.",
    )
    relationship_queries.add_argument(
        "--exclude-self-pairs",
        action="store_true",
        help="Omit same-crop queries; default keeps them for continuous cropping.",
    )
    relationship_queries.add_argument(
        "--pair-mode",
        choices=["auto", "ordered", "unordered"],
        default="auto",
        help="'auto' (default) plans unordered when every selected mode is "
             "symmetric, else ordered; 'ordered' plans n*n directed cells; "
             "'unordered' plans n(n+1)/2 neutral pair searches.",
    )
    relationship_queries.add_argument(
        "--node-mode",
        choices=["crop", "aggregate"],
        default="crop",
        help="'crop' plans crop-pair queries; 'aggregate' plans group-level "
             "(family/functional-group/host-group) queries from the node catalog.",
    )
    relationship_queries.add_argument(
        "--aggregate-node-type",
        action="append",
        choices=["botanical_family", "functional_group", "host_group"],
        default=None,
        help="Restrict aggregate node_mode to these node types (repeatable). "
             "Default: all three. Use 'functional_group' for the lean backbone.",
    )
    relationship_queries.set_defaults(func=plan_relationship_queries_command)

    relationship_discover = subparsers.add_parser(
        "discover-relationships",
        help="Execute relationship query discovery into exploration/relationships/discovery/<run_id>",
    )
    relationship_discover.add_argument("run_id", help="Relationship discovery run identifier")
    relationship_discover.add_argument(
        "--crop-dir",
        default=DEFAULT_CROP_DIR,
        help="Crop profile directory",
    )
    relationship_discover.add_argument(
        "--vocabulary",
        default=DEFAULT_VOCABULARY_PATH,
        help="Relationship vocabulary path",
    )
    relationship_discover.add_argument(
        "--mode",
        action="append",
        default=None,
        help="Relationship mode to discover; repeatable. Defaults to vocabulary default modes.",
    )
    relationship_discover.add_argument(
        "--source-tier-policy-path",
        default=DEFAULT_SOURCE_TIER_POLICY_PATH,
        help="Source-tier policy path",
    )
    relationship_discover.add_argument(
        "--source-tier-policy-id",
        default="",
        help="Source-tier policy id; defaults to the policy manifest default.",
    )
    relationship_discover.add_argument(
        "--source-tier-id",
        action="append",
        default=None,
        help="Specific source tier id to use; repeatable.",
    )
    relationship_discover.add_argument(
        "--queries-per-pair",
        type=int,
        default=1,
        help="Max templates per crop pair and relationship mode.",
    )
    relationship_discover.add_argument(
        "--query-terms-per-source-tier",
        type=int,
        default=3,
        help="Source-tier vocabulary terms appended to each query.",
    )
    relationship_discover.add_argument(
        "--max-pairs",
        type=int,
        default=None,
        help="Optional deterministic pair cap for smoke runs or larger crop universes.",
    )
    relationship_discover.add_argument(
        "--max-results-per-query",
        type=int,
        default=5,
        help="Provider results requested per query.",
    )
    relationship_discover.add_argument(
        "--limit-queries",
        type=int,
        default=None,
        help="Optional deterministic query cap for smoke runs.",
    )
    relationship_discover.add_argument(
        "--region",
        default="global",
        help="Region term to append; 'global' appends no region.",
    )
    relationship_discover.add_argument(
        "--exclude-self-pairs",
        action="store_true",
        help="Omit same-crop queries; default keeps them for continuous cropping.",
    )
    relationship_discover.add_argument(
        "--pair-mode",
        choices=["auto", "ordered", "unordered"],
        default="auto",
        help="'auto' (default) searches unordered when every selected mode is "
             "symmetric, else ordered; 'ordered' searches n*n directed cells; "
             "'unordered' searches n(n+1)/2 neutral pairs.",
    )
    relationship_discover.add_argument(
        "--node-mode",
        choices=["crop", "aggregate"],
        default="crop",
        help="'crop' searches crop pairs; 'aggregate' searches group-level "
             "(family/functional-group/host-group) pairs and defaults to "
             "textbook/institution/extension tiers.",
    )
    relationship_discover.add_argument(
        "--aggregate-node-type",
        action="append",
        choices=["botanical_family", "functional_group", "host_group"],
        default=None,
        help="Restrict aggregate node_mode to these node types (repeatable). "
             "Default: all three. Use 'functional_group' for the lean backbone.",
    )
    relationship_discover.set_defaults(func=discover_relationships_command)

    relationship_graph = subparsers.add_parser(
        "build-relationship-graph",
        help="Build the hybrid relationship evidence graph from validated claims",
    )
    relationship_graph.add_argument("run_id", help="Relationship claims run identifier")
    relationship_graph.set_defaults(func=build_relationship_graph_command)

    resolve_relationship = subparsers.add_parser(
        "resolve-crop-relationship",
        help="Resolve a crop pair from exact, group, and host-risk evidence",
    )
    resolve_relationship.add_argument("run_id", help="Relationship graph run identifier")
    resolve_relationship.add_argument("--subject", required=True, help="Subject crop or alias")
    resolve_relationship.add_argument("--object", required=True, help="Object crop or alias")
    resolve_relationship.add_argument(
        "--mode",
        default="rotation",
        help="Relationship mode to resolve; defaults to rotation.",
    )
    resolve_relationship.set_defaults(func=resolve_crop_relationship_command)

    rel_coverage = subparsers.add_parser(
        "relationship-coverage-report",
        help="Cross-run, tier-aware coverage: answerable pairs + peer-reviewed vs backbone grade",
    )
    rel_coverage.add_argument(
        "--run-id",
        action="append",
        required=True,
        help="Relationship run id to include (repeatable; claims are merged across runs).",
    )
    rel_coverage.add_argument(
        "--mode",
        action="append",
        default=None,
        help="Relationship mode(s) to report (repeatable). Default: rotation + intercrop.",
    )
    rel_coverage.add_argument("--crop-dir", default=DEFAULT_CROP_DIR, help="Crop profile directory")
    rel_coverage.add_argument("--label", default=None, help="Output label for coverage-<label>.json")
    rel_coverage.set_defaults(func=relationship_coverage_report_command)

    rel_select = subparsers.add_parser(
        "select-relationship-fetch",
        help="Balance the relationship discovery ledger into a fetch queue (dedup by relationship_source_key)",
    )
    rel_select.add_argument("run_id", help="Relationship discovery run id")
    rel_select.add_argument("--policy", default="config/fetch-policy/default.json", help="Fetch policy path")
    rel_select.set_defaults(func=select_relationship_fetch_command)

    rel_fetch = subparsers.add_parser(
        "fetch-relationships",
        help="Fetch+parse the selected relationship queue into pair-tagged raw captures",
    )
    rel_fetch.add_argument("run_id", help="Relationship discovery run id")
    rel_fetch.add_argument("--crop", default="", help="Crop (for claim-relevance during parsing)")
    rel_fetch.add_argument("--resume", action="store_true", help="Reuse HTTP cache; skip existing captures")
    rel_fetch.add_argument("--limit", type=int, default=None, help="Cap selected rows fetched")
    rel_fetch.set_defaults(func=fetch_relationships_command)

    rel_corpus = subparsers.add_parser(
        "build-relationship-corpus",
        help="Build the relationship corpus (documents/blocks/blobs + relationship_hits.jsonl)",
    )
    rel_corpus.add_argument("run_id", help="Relationship run id")
    rel_corpus.set_defaults(func=build_relationship_corpus_command)

    rel_refs = subparsers.add_parser(
        "fetch-crop-references",
        help="Fetch each crop's main reference article (Wikipedia) into the relationship raw layer, bypassing pair-template discovery",
    )
    rel_refs.add_argument("run_id", help="Relationship run id")
    rel_refs.add_argument("--crop-dir", default=DEFAULT_CROP_DIR, help="Crop profile directory")
    rel_refs.add_argument("--crop", action="append", default=None, help="Restrict to specific crop id(s); repeatable.")
    rel_refs.add_argument("--limit", type=int, default=None, help="Cap number of crops fetched")
    rel_refs.set_defaults(func=fetch_crop_references_command)

    rel_validate = subparsers.add_parser(
        "validate-relationship-claims",
        help="Validate extracted relationship claims against crop-relationship-claim.schema.json",
    )
    rel_validate.add_argument("run_id", help="Relationship run id")
    rel_validate.set_defaults(func=validate_relationship_claims_command)

    rel_populate = subparsers.add_parser(
        "populate-relationship-matrix",
        help="Populate the crop×crop matrix cells (mode_statuses) from claims + discovery ledger",
    )
    rel_populate.add_argument("run_id", help="Relationship run id")
    rel_populate.add_argument("--mode", action="append", default=None, help="Restrict to mode id(s)")
    rel_populate.set_defaults(func=populate_relationship_matrix_command)

    rel_eval = subparsers.add_parser(
        "eval-relationships",
        help="Score the populated matrix against the relationship gold set",
    )
    rel_eval.add_argument("run_id", help="Relationship run id")
    rel_eval.set_defaults(func=eval_relationships_command)

    units = subparsers.add_parser(
        "normalize-units",
        help="Unit-normalization cleanup (°F→°C convert; flag unit-mixed params) on a normalized run",
    )
    units.add_argument("run_id", help="Run id")
    units.add_argument("--claims-subdir", default="normalized", help="Input claims subdir")
    units.add_argument("--output-subdir", default="normalized_units", help="Output subdir")
    units.set_defaults(func=normalize_units_command)

    calibration = subparsers.add_parser(
        "render-calibration",
        help="Render a per-crop local-calibration vault note (soil-test/season gaps)",
    )
    calibration.add_argument("run_id", help="Run id")
    calibration.add_argument("--crop", required=True, help="Crop (e.g. wheat)")
    calibration.add_argument("--region", required=True, help="Region label (e.g. Freiburg)")
    calibration.add_argument("--vault", default=DEFAULT_VAULT, help="Obsidian vault root path")
    calibration.add_argument("--claims-subdir", default="normalized", help="Claims subdir")
    calibration.add_argument("--dry-run", action="store_true", help="Print the note; write nothing")
    calibration.set_defaults(func=render_calibration_command)

    extract = subparsers.add_parser(
        "extract-run",
        help="Extract claims from a run's raw captures via the LLM extractor (fixture backend by default)",
    )
    extract.add_argument("run_id", help="Exploration run identifier")
    extract.add_argument(
        "--backend",
        default="fixture",
        choices=["fixture", "local", "llm"],
        help="Extraction backend: 'fixture' (deterministic, no network), "
        "'local' (Ollama, free, no API), or 'llm' (Claude, needs ANTHROPIC_API_KEY)",
    )
    extract.add_argument(
        "--model",
        default="claude-opus-4-8",
        help="Model id (local backend defaults to llama3.1 when this is left at the Claude default)",
    )
    extract.add_argument(
        "--host",
        default=None,
        help="Ollama host for the local backend (default $OLLAMA_HOST or http://localhost:11434)",
    )
    extract.add_argument(
        "--manifest",
        default="config/parameters/core-crop-parameters.json",
        help="Parameter manifest path",
    )
    extract.set_defaults(func=extract_run_command)

    normalize = subparsers.add_parser(
        "normalize-run",
        help="Normalize raw capture artifacts into load-ready claims",
    )
    normalize.add_argument("run_id", help="Exploration run identifier")
    normalize.add_argument(
        "--config",
        default="config/hooks/default.json",
        help="Hook config path",
    )
    normalize.add_argument(
        "--from-llm",
        action="store_true",
        help="Build claims from the LLM extractor instead of the heuristic path",
    )
    normalize.add_argument(
        "--backend",
        default="fixture",
        choices=["fixture", "local", "llm"],
        help="Extraction backend when --from-llm is set: fixture (no network), local (Ollama, free), llm (Claude)",
    )
    normalize.add_argument(
        "--model",
        default="claude-opus-4-8",
        help="Model id (local backend defaults to llama3.1 when this is left at the Claude default)",
    )
    normalize.add_argument(
        "--host",
        default=None,
        help="Ollama host for the local backend (default $OLLAMA_HOST or http://localhost:11434)",
    )
    normalize.add_argument(
        "--output-subdir",
        default="normalized",
        help="Output subdir under exploration/ (use a separate dir to preserve heuristic baselines)",
    )
    normalize.set_defaults(func=normalize_run_command)

    build_corpus_parser = subparsers.add_parser(
        "build-corpus",
        help="Build a durable, deduplicated document/block corpus from a run's raw captures",
    )
    build_corpus_parser.add_argument("run_id", help="Corpus run identifier")
    build_corpus_parser.add_argument(
        "--source-run",
        default=None,
        help="Raw run to read captures from (defaults to run_id)",
    )
    build_corpus_parser.set_defaults(func=build_corpus_command)

    corpus_qa_parser = subparsers.add_parser(
        "corpus-qa",
        help="Generate the raw corpus QA report that gates the Opus extraction pass",
    )
    corpus_qa_parser.add_argument("run_id", help="Corpus run identifier")
    corpus_qa_parser.set_defaults(func=corpus_qa_command)

    backfill_parser = subparsers.add_parser(
        "backfill-corpus",
        help="Resolve OA full text for metadata-only docs and exclude junk DOIs from the Opus input",
    )
    backfill_parser.add_argument("run_id", help="Corpus run identifier")
    backfill_parser.add_argument(
        "--email", default="research@example.org",
        help="Contact email for the Unpaywall/OpenAlex polite pool",
    )
    backfill_parser.add_argument(
        "--limit", type=int, default=None, help="Max metadata-only documents to process",
    )
    backfill_parser.set_defaults(func=backfill_corpus_command)

    render_vault_parser = subparsers.add_parser(
        "render-vault",
        help="Render normalized claims into tagged, interlinked Obsidian notes",
    )
    render_vault_parser.add_argument("run_id", help="Exploration run identifier")
    render_vault_parser.add_argument("--vault", default=DEFAULT_VAULT, help="Obsidian vault root path")
    render_vault_parser.add_argument(
        "--subdir", default="DeCropsResearch/crop_science", help="Subfolder under the vault (only writes here)"
    )
    render_vault_parser.add_argument(
        "--claims-subdir", default="normalized",
        help="exploration/<subdir>/<run> to read claims from (e.g. normalized or normalized_llm)",
    )
    render_vault_parser.add_argument("--dry-run", action="store_true", help="Print the file plan; write nothing")
    render_vault_parser.add_argument("--prune", action="store_true", help="Remove stale generated notes")
    render_vault_parser.set_defaults(func=render_vault_command)

    review = subparsers.add_parser(
        "review-run",
        help="Score normalized claims and create promotion review artifacts",
    )
    review.add_argument("run_id", help="Exploration run identifier")
    review.set_defaults(func=review_run_command)

    promote = subparsers.add_parser(
        "promote-run",
        help="Promote reviewed canonical/regional candidates into durable memory artifacts",
    )
    promote.add_argument("run_id", help="Exploration run identifier")
    promote.set_defaults(func=promote_run_command)

    coverage = subparsers.add_parser(
        "coverage-run",
        help="Report parameter coverage from normalized, reviewed, and promoted claims",
    )
    coverage.add_argument("run_id", help="Exploration run identifier")
    coverage.set_defaults(func=coverage_run_command)

    eval_extraction_parser = subparsers.add_parser(
        "eval-extraction",
        help="Score cached Opus extractions against the extraction gold set",
    )
    eval_extraction_parser.add_argument("run_id", help="Run identifier (llm_cache run)")
    eval_extraction_parser.add_argument(
        "--gold-dir", default=None, help="Gold set dir (default tests/golden/extraction)"
    )
    eval_extraction_parser.set_defaults(func=eval_extraction_command)

    eval_retrieval_parser = subparsers.add_parser(
        "eval-retrieval",
        help="Score a discovery ledger + fetch queue against the retrieval gold set",
    )
    eval_retrieval_parser.add_argument("run_id", help="Run identifier (discovery run)")
    eval_retrieval_parser.add_argument(
        "--gold-dir", default=None, help="Gold set dir (default tests/golden/retrieval)"
    )
    eval_retrieval_parser.set_defaults(func=eval_retrieval_command)

    postgres = subparsers.add_parser(
        "load-postgres",
        help="Export normalized claims to PostgreSQL SQL and optionally load them",
    )
    postgres.add_argument("run_id", help="Exploration run identifier")
    postgres.set_defaults(func=load_postgres_command)

    handoff = subparsers.add_parser(
        "write-handoff",
        help="Refresh docs/HANDOFF.md from current repo artifacts",
    )
    handoff.add_argument(
        "--output",
        default="docs/HANDOFF.md",
        help="Handoff output path",
    )
    handoff.set_defaults(func=write_handoff_command)

    capability_map = subparsers.add_parser(
        "write-capability-map",
        help="Refresh docs/CAPABILITY_MAP.md from current repo artifacts",
    )
    capability_map.add_argument(
        "--output",
        default="docs/CAPABILITY_MAP.md",
        help="Capability map output path",
    )
    capability_map.set_defaults(func=write_capability_map_command)

    return cli


def main() -> int:
    cli = parser()
    args = cli.parse_args()
    try:
        return args.func(args)
    except Exception as exc:  # pragma: no cover - CLI guardrail
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
