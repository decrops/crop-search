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
from .review import ClaimReviewRunner
from .schema_registry import SchemaRegistry


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

    DEFAULT_VAULT = (
        "/Users/admin/Library/Mobile Documents/iCloud~md~obsidian/Documents/Tino_deCrops"
    )
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
