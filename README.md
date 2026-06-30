# Crop Search Foundation

This repo now includes the first implementation layer for a provenance-aware crop knowledge system.

The starting principle comes from [SEARCH_EXPLORATION_SUMMARY.md](/Users/admin/dev/crop-search/SEARCH_EXPLORATION_SUMMARY.md:4): search is part of the ingestion pipeline, raw capture is a staging layer, and normalized provenance-backed records become the system of record.

## What is scaffolded

- A framework for context engineering with typed context packs
- A memory model split into ephemeral run, working, and durable memory
- Hook contracts for each ingestion stage
- MCP server manifest templates for the required tool surface
- JSON schemas for raw capture, hook events, memory, and normalized claims
- A small Python CLI to validate artifacts and exercise hooks locally

## Repo layout

- `docs/ARCHITECTURE.md`: framework decisions and component boundaries
- `docs/ROADMAP.md`: phased implementation plan from exploration to production
- `schemas/`: JSON schemas for core contracts
- `config/parameters/core-crop-parameters.json`: generic crop physiology and management parameter manifest
- `config/crops/`: crop profiles that adapt generic parameters to a target crop
- `config/relationships/relationship-vocabulary.json`: crop-to-crop relationship modes, query templates, effect labels, and mechanism tags
- `config/hooks/default.json`: built-in hook pipeline
- `config/mcp/servers.example.json`: MCP server manifest template
- `config/mcp/servers.local.json`: live web-backed tool bindings for the pilot run
- `config/mcp/servers.fixtures.json`: offline fixture-backed fallback bindings
- `config/runs/`: manifest-driven exploration run definitions, including corn, wheat, rice, sunflower, and tomato pilots
- `fixtures/`: local source fixtures for development runs
- `templates/`: starter context packs and memory records
- `src/crop_search_framework/`: validation and hook runner code
- `exploration/`: raw and normalized staging areas
- `exploration/review/`: claim promotion reviews and source scorecards
- `exploration/coverage/`: parameter coverage reports for manifest-driven runs
- `data/postgres/schema.sql`: first relational load target

## Quick start

For continuity, start future sessions with `docs/HANDOFF.md` and `docs/CAPABILITY_MAP.md`. Agents should follow `AGENTS.md`, and any session that changes code, configs, generated artifacts, docs, or pipeline capabilities should refresh the generated memory files with `crop-framework write-capability-map` and `crop-framework write-handoff`.

1. Create a virtual environment and install the package.
2. Validate the included templates.
3. Run the live pilot exploration.
4. Normalize the captures.
5. Review normalized claims for promotion readiness.
6. Promote reviewed canonical and regional candidates into durable memory.
7. Export PostgreSQL load SQL.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
crop-framework validate schemas/context-pack.schema.json templates/context/mission.json
crop-framework plan-queries --run-config config/runs/pilot-us-corn-iowa.json
crop-framework write-relationship-matrix
crop-framework plan-relationship-queries --mode rotation --queries-per-pair 1 --output exploration/relationships/query_plans/rotation-current.json
crop-framework discover-relationships relationship-rotation-smoke --mode rotation --queries-per-pair 1 --limit-queries 5
# Hybrid lane: plan unordered minor-crop pair searches (n(n+1)/2 instead of n*n),
# then build the evidence graph from validated claims and resolve a pair.
crop-framework plan-relationship-queries --mode rotation --pair-mode unordered --queries-per-pair 1
crop-framework build-relationship-graph relationship-rotation-smoke
crop-framework resolve-crop-relationship relationship-rotation-smoke --subject "pak choi" --object cabbage --mode rotation
# Intercropping (symmetric): --pair-mode auto plans the n(n+1)/2 unordered set;
# evidence is canonicalized so one claim answers both crop orderings.
crop-framework discover-relationships intercrop-smoke --mode intercrop --queries-per-pair 1 --limit-queries 5
crop-framework resolve-crop-relationship intercrop-smoke --subject soybean --object corn --mode intercrop
# Reference articles: fetch each crop's main encyclopedia page (rotation prose)
# directly, bypassing hostile-publisher PDFs and noisy pair-template search.
crop-framework fetch-crop-references crop-refs --crop sugar_beet --crop sunflower
crop-framework build-relationship-corpus crop-refs
crop-framework run-exploration --run-config config/runs/pilot-us-corn-iowa.json --manifest config/mcp/servers.local.json
# Durable raw layer: dedupe captures into a content-addressed document/block store,
# then a QA report that gates the (expensive) Opus extraction pass.
crop-framework build-corpus pilot-us-corn-iowa-001
# Backfill: resolve open-access full text for metadata-only DOIs and exclude
# non-article DOIs (supplements/reviews/datasets) from the Opus input set.
crop-framework backfill-corpus pilot-us-corn-iowa-001 --email you@example.org
crop-framework corpus-qa pilot-us-corn-iowa-001
crop-framework extract-run pilot-us-corn-iowa-001 --backend fixture
# Free local LLM extraction via Ollama (no API cost): install Ollama, `ollama serve`,
# `ollama pull llama3.1`, then:
#   crop-framework normalize-run <run_id> --from-llm --backend local
crop-framework normalize-run pilot-us-corn-iowa-001
crop-framework review-run pilot-us-corn-iowa-001
crop-framework promote-run pilot-us-corn-iowa-001
crop-framework coverage-run pilot-us-corn-iowa-001
crop-framework load-postgres pilot-us-corn-iowa-001
# Render normalized claims into tagged, interlinked Obsidian notes (dry-run first;
# only ever writes under --subdir and only overwrites files it generated).
crop-framework render-vault pilot-us-corn-iowa-001 --dry-run
crop-framework write-capability-map
crop-framework write-handoff
```

`build-corpus` collapses the `(source_url, parameter_id)` capture explosion into a content-addressed **document store** (deduped by text hash) plus a **block store** (sections/paragraphs/tables) and **query_hits** associations; `backfill-corpus` then resolves open-access full text for metadata-only DOIs (Unpaywall, then OpenAlex `best_oa_location`, fetched through an exponential-backoff/cached HTTP path) and marks non-article DOIs (supplements, peer-review stubs, datasets, F1000 recommendations) `excluded_from_opus`; `corpus-qa` emits `exploration/corpus/<run>/qa_report.{json,md}` whose gates (duplicate-text ratio, metadata-only share, background/Wikipedia share, table coverage — all computed over the post-backfill Opus input set) decide whether the corpus is ready for the Opus extraction pass. `render-vault` turns normalized claims into one atomic note per crop × parameter, plus crop/parameter/domain/source entity hubs and an index, with YAML facet `tags:` and `[[wikilinks]]`. It writes only under `--subdir` (default `DeCropsResearch/crop_science`) and only ever overwrites files carrying a `generated_by: crop-search` marker, so existing vault notes are never touched.

The local manifest uses simple stdio JSON commands that mirror the future MCP server boundaries. The current `servers.local.json` routes each source tier to free, key-less discovery APIs instead of relying on web scraping:

- **Peer-reviewed science:** OpenAlex, Crossref, Europe PMC, DOAJ
- **Textbooks/reference:** Google Books, Open Library, Internet Archive, DOAB (open-access books)
- **International institutions:** OpenAlex, Wikipedia
- **Extension/public agronomy:** OpenAlex, Internet Archive, Wikipedia
- **Industry/grower guides:** OpenAlex, Wikipedia

Every tier now resolves to at least one structured API, so the DuckDuckGo HTML scraper is only a last-resort top-up when an API returns too few results — it is no longer the primary discovery path for any tier and its rate-limits/blocks no longer stall a run. Each connector fails soft: a provider error (e.g. a Google Books `429`) is recorded in `provider_errors` while the other connectors for that tier still return results. `servers.fixtures.json` remains available for offline or deterministic development.

`review-run` is the quality gate between normalized extraction and durable knowledge promotion. It writes `exploration/review/<run_id>/review.json` with per-claim decisions, duplicate/conflict clusters, and source reliability scorecards.

`normalize-run` now uses the raw run summary for crop metadata and infers claim location from the evidence itself. Broad claims can be scoped as `global`, while country, region, state, and farm-specific claims stay separated before conflict checks and promotion review.
It also records `source_geo_scope` separately from claim applicability, so source-origin geography is preserved without over-scoping generic agronomic facts.
When a scope can be geocoded, the readout includes `geo_id`, centroid `lat`/`lon`, optional `bbox`, geocode source, and confidence. U.S. state and county scopes are backed by the 2025 Census Gazetteer; named production regions remain custom approximate regions with explicit lower-confidence provenance.

`promote-run` writes schema-validated durable claim records under `memory/durable/<run_id>/claims.json`. Only reviewed canonical, regional, and merge candidates without unresolved conflicts are promoted.

`plan-queries` renders manifest-driven search queries before a run. The generic parameter manifest defines crop physiology and management parameters once; crop profiles such as `config/crops/corn.json`, `config/crops/soybean.json`, and `config/crops/wheat.json` provide crop-specific aliases and growth-stage vocabulary.

`write-relationship-matrix` writes the crop-to-crop relationship skeleton under `exploration/relationships/matrix/`. The dense matrix is strictly `crop_id x crop_id`: with the current 7 crop profiles it has 49 ordered cells, including self-pairs for continuous cropping. `plan-relationship-queries` renders source-tier-aware pair queries for relationship modes such as rotation without fetching sources. `discover-relationships` executes those pair-aware searches into `exploration/relationships/discovery/<run_id>/results.jsonl`, preserving `subject_crop_id`, `object_crop_id`, `relationship_mode`, `ordered_pair_key`, and `canonical_relationship_key` on every ledger row. Relationship search/extraction is intentionally separate from single-crop parameter extraction.

A **hybrid evidence graph** layers a coarser, request-time lane on top of the dense matrix for minor crops and aggregate nodes, without changing the matrix. `--pair-mode unordered` plans `n(n+1)/2` direction-neutral pair searches instead of `n*n` directed cells (7→28, 25→325, 120→7260); each unordered query carries a `search_pair_key` and both `candidate_ordered_pair_keys`, so one neutral search still feeds the correct directed matrix cell after extraction. `config/relationships/node-catalog.json` defines crop, genus, botanical-family, functional-group, and host-group nodes. `build-relationship-graph <run_id>` indexes validated, evidence-bearing claims (`accepted`/`needs_review` only; `rejected`/`conflict` excluded) keyed by `(relationship_mode, subject_node, object_node)`, synthesizing crop nodes from legacy crop-only claims. `resolve-crop-relationship <run_id> --subject <crop_or_alias> --object <crop_or_alias> [--mode rotation]` then answers a pair from exact crop evidence first, then cross-group inference (botanical_family → functional_group → genus, in relationship direction — covering both same-family avoidance and cereal-after-legume), always overlaying host-risk caveats for host groups both crops share. A routing guard (`relationship_parameter_span_conflicts`) ensures the same evidence span is never emitted as both a relationship claim and a `management.rotation_recommendation` parameter claim.

Symmetric modes (`intercrop`, `strip_crop`, `mixed_crop`, `companion_crop`) are handled order-independently: claims are canonicalized to sorted endpoints when loaded, so a single intercropping claim mirrors into both ordered matrix cells and `resolve-crop-relationship --mode intercrop` answers the same for `(a,b)` and `(b,a)` — while directional modes like `rotation`/`relay_crop` stay one-directional. `--pair-mode auto` (the default) plans the `n(n+1)/2` unordered set when every selected mode is symmetric and the `n*n` ordered set otherwise.

The graph's inference lane is fed by `--node-mode aggregate` on `plan-relationship-queries` / `discover-relationships`: instead of crop-pair searches it plans **group-level** queries (e.g. `cereal after legume rotation nitrogen credit`, `brassicaceae shared host disease carryover`) from the node catalog's family/functional-group/host-group nodes, steered by default to the textbook/institution/extension tiers where such principles are stated. Node identity (`subject_node_type/id`) is carried through discovery → fetch queue → captures → corpus so an extracted group statement becomes a `functional_group`/`botanical_family`/`host_group` claim that the resolver can infer minor-crop pairs from. Crop-specific evidence still yields direct crop claims — the extraction prompt enforces the level-of-evidence rule. Evidence-backed extraction, review, and human acceptance remain manual in-session Opus gates.

Run configs can include `source_seeds` for trusted live source URLs (each optionally scoped to a `source_tier_id` and specific `parameter_ids`). The `seed_mode` field controls when they are used:

- `"fallback"` (default): seeds fire only for a query whose live search returned nothing — a safety net against rate-limits/blocks.
- `"augment"`: matching seeds are always merged alongside live search results (de-duplicated by URL, live results win).

`augment` mode is how the non-scholarly tiers get real grower-facing content. The free per-tier APIs cover science and books well, but `international_institution`, `extension_publication`, and `industry_grower_guide` would otherwise be scholarly-biased (OpenAlex/Wikipedia). `config/runs/pilot-global-wheat.json` uses `seed_mode: "augment"` with curated extension (K-State, SDSU, Nebraska, Oregon State, Michigan State), institutional (FAO, CIMMYT), and grower-guide (AHDB) wheat seeds, each mapped to the gap parameters (planting, nutrients, water, phenology, harvest) that pure search under-covers.

Run configs can also include `source_tier_policy_path`. The default source-tier policy searches legally accessible evidence across peer-reviewed science, open textbook/reference material, international institutions, extension/public agronomy publications, and industry/grower guides. The global run configs under `config/runs/pilot-global-*.json` use this policy and avoid adding `United States` to the generated queries.

`coverage-run` compares requested parameters with normalized, reviewed, and promoted records so we can see which physiology or management parameters were found, promoted, missing, or still stuck in review. It also reports source-tier coverage, including whether peer-reviewed science or textbook/reference evidence supports each parameter.

`write-capability-map` regenerates `docs/CAPABILITY_MAP.md`, the living inventory of what the pipeline can do now, what is configured but not yet exercised, what is partial, and what remains missing. Keep it current alongside `write-handoff` so a new session can restart without context rot.

If you want direct database loading instead of SQL export, install the optional PostgreSQL extra and set `POSTGRES_DSN`:

```bash
pip install -e .[pg]
export POSTGRES_DSN='postgresql://user:pass@host:5432/dbname'
crop-framework load-postgres pilot-us-corn-iowa-001
```

For local verification, you can start a disposable PostgreSQL instance with Docker:

```bash
docker compose -f docker-compose.postgres.yml up -d
export POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:55432/cropsearch'
crop-framework load-postgres pilot-us-corn-iowa-001
docker compose -f docker-compose.postgres.yml down
```

## First implementation priorities

1. Improve source selection and filtering for higher-signal scientific, textbook, international-institution, agronomy, and extension results.
2. Promote stable extraction patterns into the normalized claim model.
3. Add stronger unit normalization, date parsing, and conflict modeling.
4. Replace the current search-provider fallback with a durable production search provider or provider rotation.
5. Use `review-run` to promote only high-confidence canonical or regional candidates into durable memory.
6. Keep fixture-backed tests and CI green before expanding beyond the pilot.
7. Expand crop coverage by adding crop profiles, not by rewriting the core parameter manifest.
