# Roadmap

## Phase 0: Foundation

- Finalize context pack taxonomy and validation rules
- Wire the default hook chain into all run stages
- Replace placeholder MCP definitions with the chosen tool implementations
- Decide where durable memory will live

## Phase 1: Exploration sandbox

- Add search runners for a small crop and geography matrix
- Start with the implemented `pilot-us-corn-iowa` run and refine the live search/fetch/parser stack
- Generate search queries from the crop parameter manifest where available
- Use source seeds as a recoverable fallback when live search providers block or return no usable results
- Persist raw captures and failed parses as immutable artifacts
- Measure source heterogeneity and parsing failure modes

## Phase 2: Extraction contract

- Stabilize the normalized claim schema from observed raw captures
- Infer claim-level crop and location scope from run metadata and evidence text
- Preserve source-origin geography separately from claim applicability
- Geocode claim and source scopes to stable IDs, centroid latitude/longitude, and optional bounding boxes
- Back U.S. state and county geocoding with Census Gazetteer records while keeping named production regions explicitly approximate
- Add unit normalization, confidence rules, and conflict handling
- Create promotion rules from working memory into durable memory through `review-run`
- Use `attribute_subtype` to separate related but non-conflicting facts before review
- Carry `parameter_id` through normalized, review, durable, and coverage records

## Phase 3: Production path

- Add PostgreSQL migrations for validated normalized and durable claims
- Load only records that pass provenance, review, and schema validation
- Keep baseline crop facts distinct from regional augmentations

## Phase 4: Evaluation and scale

- Track provenance completeness, extraction precision, and conflict rates
- Add source reliability scorecards through the review report
- Track parameter coverage: promoted, candidate-only, needs-review, missing
- Search across accessible source tiers, including peer-reviewed science, open textbook/reference material, international institutions, extension/public agronomy publications, and industry/grower guides
- Evaluate a durable production search provider or provider rotation before scaling live runs
- Expand coverage only after the pilot pipeline is stable
