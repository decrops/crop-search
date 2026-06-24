# Architecture

## Product framing

The system is built around evidence-backed crop claims, not around isolated tables. Search and document retrieval are part of ingestion, so the framework optimizes for provenance, validation, and repeatability before scale.

## Core layers

### 1. Context engineering

The agent runtime should load small, typed context packs instead of a single giant prompt.

- `mission`: product invariants and non-negotiable rules
- `source-policy`: provenance, citation, and trust requirements
- `entity-schema`: crop, region, claim, and evidence concepts
- `run-brief`: the current exploration or production job
- `handoff-summary`: state needed for continuity across runs

Each context pack should be versioned and validated against `schemas/context-pack.schema.json`.

### 2. Memory management

Memory is intentionally split by purpose and retention.

- `ephemeral_run`: transient state for the current run
- `working`: candidate claims, unresolved mappings, and conflict notes
- `durable`: promoted knowledge such as source heuristics, schema decisions, and normalization rules

Only validated, reviewed learnings should move into durable memory.

### 3. Hooks

Hooks enforce consistency at the boundaries of the pipeline.

- `pre-search`
- `post-fetch`
- `post-extract`
- `pre-normalize`
- `pre-load`
- `on-failure`

The default hook chain logs every event, validates payloads where possible, and writes artifacts for later inspection.

### 4. Data shape

The framework keeps two storage modes separate.

- Raw exploration capture for discovery and debugging
- Normalized claims for production-ready loading

Raw capture remains staging and should never be treated as the source of truth.
Normalized claims carry claim-level location scope inferred from evidence text, not just the run query. This keeps global crop facts, country or region claims, and farm/state observations separate before conflict checks and durable promotion.
Normalized claims also carry `source_geo_scope`, which records source/document geography separately from `location_scope`. This prevents an institutional source such as a Georgia extension handbook from automatically turning every tomato claim into a Georgia-only recommendation unless the claim evidence itself says so.
Both claim and source scopes are geocoded when a local gazetteer has a match. Geocoded scopes carry a stable `geo_id`, EPSG:4326 centroid latitude/longitude, optional bounding box, geocode source, and confidence. U.S. states and counties use 2025 Census Gazetteer internal points with `census:*` IDs. Named crop production regions use custom approximate region records because those boundaries vary by source and crop context; named farm coordinates can be exact points when verified.
Normalized claims also carry `attribute_subtype`, which is the conflict key for quantitative claims. For example, `base_temperature`, `stress_temperature`, `survival_temperature`, and `soil_emergence_temperature` are not treated as contradictions just because they are all temperature facts.

### 4.1 Durable promotion

`review-run` scores and classifies normalized claims. `promote-run` then writes the reviewed canonical, regional, and merge candidates into durable memory artifacts under `memory/durable/<run_id>/claims.json`.

Promotion is intentionally separate from normalization: unresolved conflicts, seasonal observations, rejects, and broad manual-review candidates stay in working memory until the extraction rules improve.

### 4.2 Parameter manifest

The search space is now driven by a generic crop parameter manifest instead of only hand-written crop queries.

- `config/parameters/core-crop-parameters.json` defines reusable physiology and management parameters.
- `config/crops/<crop>.json` provides crop aliases, growth-stage terms, and source-bias terms.
- `config/source-tiers/default.json` defines source tiers for peer-reviewed science, open textbook/reference material, international institutions, extension/public agronomy publications, and industry/grower guides.
- `plan-queries` renders parameter-specific and source-tier-specific web searches for the chosen crop and region.
- Normalized, reviewed, promoted, and coverage records carry `parameter_id` so downstream storage can answer parameter-coverage questions directly.

The manifest is crop-neutral by design. Crop profiles adapt the manifest to corn, soybean, wheat, rice, cotton, or future crops without turning the core taxonomy into a corn-specific list.

Run configs may also include `source_seeds`, which are trusted live URLs used only when search returns no usable results or fails. This keeps the pipeline moving with real web retrieval while preserving the same fetch, parse, normalize, review, promote, and coverage contracts. Source seeds are not a replacement for production search; they are a resilience layer and a way to test extraction against known high-signal source families.

Global run configs use `region_scope=global`, tier-specific query terms, and no source-seed fallback by default. That prevents the fallback source list from quietly pulling the search back into a U.S.-centric evidence base.

### 5. MCP server surface

The minimum required tool surface is:

- repository access
- git history
- web search and page retrieval
- document parsing for HTML and PDFs
- raw artifact storage
- schema validation
- PostgreSQL access
- observability and batch orchestration

The repo includes a manifest template in `config/mcp/servers.example.json` so these tools can be wired into the agent runtime consistently.

For local development, `config/mcp/servers.local.json` uses stdio JSON commands that preserve the same boundaries as the intended MCP tool surface while talking to the live web. `config/mcp/servers.fixtures.json` keeps an offline fallback path for deterministic testing.
