# Aggregate Relationship Discovery — Plan

> **Status: IMPLEMENTED (2026-06-29).** All steps (1, 2, 3, 4, 4b, 5, 6, 7)
> shipped; suite green at 153 tests incl. `tests/test_relationship_aggregate_discovery.py`.
> Verified live end-to-end through discovery + fetch-queue selection. See the
> `2026-06-29: Aggregate relationship discovery` entry in
> [docs/IMPLEMENTATION_LOG.md](../docs/IMPLEMENTATION_LOG.md).

## The finding, in detail

The hybrid lane shipped the **consumption** side of aggregate evidence (node
catalog, graph `aggregate` index, resolver `inferred_from_group`) but not the
**production** side. Nothing in the live pipeline ever emits an aggregate claim,
so `graph["aggregate"]` is structurally empty and minor-crop inference is
unreachable from real data. This is not a data-availability accident; it is a
gap in three linked layers:

1. **Pair generation is crop-only.** `build_relationship_query_plan` iterates
   `ordered_crop_pairs` / `unordered_crop_pairs` over
   `load_crop_universe(config/crops)`. The node catalog's `functional_group`,
   `botanical_family`, and `host_group` nodes are never used to generate a
   query. So no search ever *targets* group-level evidence.

2. **Templates render specific crop names.** Every template interpolates
   `{subject_crop}` / `{object_crop}` from `CropNode.search_term` (the crop's
   first alias — "wheat", "soybean"). A rotation query is literally
   `wheat rotation with soybean nitrogen credit`. Search engines answer a
   two-named-crop query with **crop-specific primary research** (field trials),
   because that is what the query asks for. The functional-group principle
   ("cereals after legumes gain a nitrogen credit") is phrased at the group
   level in textbooks/reviews and is never retrieved.

3. **Tier steering points at primary research.** Discovery defaults to
   `peer_reviewed_science` / `extension_publication`. Primary science is the
   *least* likely tier to state a group-level generalization; principles live in
   `textbook_reference` and `international_institution` (FAO/CGIAR) sources.

The downstream extractor then behaves correctly and makes the gap visible: the
extraction contract forbids emitting an aggregate claim from crop-specific
evidence ("use aggregate fields *when the evidence is aggregate*"). So even a
paper that mentions soybean's nitrogen contribution yields an honest **direct**
crop claim, never a `functional_group` one. Observed live in run
`rel-agg-legume-001`: discovery → fetch → extraction all succeeded, produced a
real `corn|soybean` direct claim, and left `graph["aggregate"] == {}`.

**Net effect:** the resolver can return `direct_evidence` (dense matrix works)
but can never reach `inferred_from_group` for a crop outside the dense matrix —
which was the entire reason the hybrid lane exists.

## Fix strategy

Add an **aggregate-node discovery + extraction path** that mirrors the existing
crop-pair path and feeds the already-built resolver. Schema-first, non-breaking,
and gated like the rest of the relationship lane (no auto-accept; live crawl and
Opus extraction stay manual). The aggregate universe is small —
6 functional groups, 6 families, 2 host groups — so the planned search volume is
tiny next to the crop matrix.

### Design decisions

- **Reuse the node catalog as the aggregate vocabulary.** Aggregate nodes
  already carry `aliases` (legume → "legume/legumes/pulse crops", brassicaceae →
  "brassica family/crucifers"), so a group `search_term` is available with no new
  config. Pairs are generated *within a node type*: functional_group ×
  functional_group, botanical_family × botanical_family, and host_group
  self/overlay queries.
- **Group-level templates, not crop templates.** Add aggregate query templates
  to the vocabulary, rendered from `{subject_group}` / `{object_group}`, e.g.
  rotation `"{subject_group} after {object_group} rotation nitrogen credit"`,
  family `"{subject_family} {object_family} same family disease carryover"`,
  host-risk `"{host_group} shared host disease rotation risk"`.
- **Steer tiers toward principle sources.** Default aggregate discovery to
  `textbook_reference`, `international_institution`, and `extension_publication`;
  exclude `peer_reviewed_science` from the default aggregate tier set (it can
  stay opt-in).
- **Carry node identity end-to-end**, exactly as crop pairs are carried today:
  discovery row → fetch queue → fetch capture → corpus hit → extraction context
  all gain `subject_node_type/id` + `object_node_type/id`, so the extractor knows
  to emit an aggregate claim and the resolver's
  `(mode, subject_node, object_node)` key lines up.
- **Introduce a mode-agnostic label field.** Both crop and aggregate items carry
  a single `subject_search_label` / `object_search_label` (the crop's search
  term in crop mode, the group's first alias in aggregate mode). Every consumer
  that today reaches for `subject_crop_label` reads the mode-agnostic label
  instead, so nothing downstream has to branch on `node_mode`. This is the
  cleanest way to satisfy the two plumbing cautions below.

### Plumbing cautions (must-not-miss — every crop-id reader must tolerate node-only rows)

The crop fields stop being guaranteed once aggregate rows exist. Two stages read
them unconditionally today and will break with a `KeyError` / wrong arg unless
updated in lockstep with the planner:

1. **Fetch-queue selection** — `select_relationship_fetch()` builds its candidate
   dict with hard `row["subject_crop_id"]` / `row["object_crop_id"]` access
   (`src/crop_search_framework/relationship_pipeline.py`, ~L97-98). It must use
   `.get()`, carry `subject_node_type/id` + `object_node_type/id` onto the
   candidate/queue row, and keep balancing on `relationship_source_key` /
   `canonical_relationship_key` (already node-aware), not on crop ids. Covered in
   **Step 4b**.
2. **Connector label** — `discover_relationships()` passes
   `crop=item["subject_crop_label"].lower()` into `connector_results_for_tier`
   (`src/crop_search_framework/relationships.py`, ~L496-498). Aggregate items
   have no crop label; this must read the mode-agnostic `subject_search_label`,
   or the connector call breaks. Covered in **Step 4**.

General rule for the implementer: grep for `subject_crop_id`, `object_crop_id`,
and `subject_crop_label` across the relationship lane and make each reader either
`.get()`-tolerant or switched to the node / mode-agnostic field before enabling
aggregate runs.

## Steps

### Step 1 — Schema: allow node-typed query items
File: `schemas/crop-relationship-query-plan.schema.json`

`$defs.query_item` hard-requires `subject_crop_id` / `object_crop_id` /
`*_crop_label`. Mirror the fix already applied to the claim schema: add optional
`subject_node_type/id` + `object_node_type/id` and replace the unconditional
crop-field requirement with per-side `anyOf` (crop fields **or** node type+id).
Add a top-level `node_mode` enum `["crop","aggregate"]`.

### Step 2 — Vocabulary: aggregate templates
File: `config/relationships/relationship-vocabulary.json` (+ schema if needed)

Add an `aggregate_query_templates` block per relevant mode (rotation,
continuous_cropping, cover_crop, intercrop, companion_crop) plus a small set of
host-risk templates. Keep `{subject_group}`/`{object_group}`/`{host_group}`
placeholders distinct from the crop placeholders so the renderer can tell them
apart.

### Step 3 — Planner: aggregate pair generation + rendering
File: `src/crop_search_framework/relationships.py`

- Add an `AggregateNode` view over the node catalog (id, type, label, aliases →
  `search_term`).
- Add `aggregate_node_pairs(catalog, node_type, pair_mode)` and a
  `node_mode="crop"|"aggregate"` parameter to `build_relationship_query_plan`.
- When `node_mode="aggregate"`, build queries from group templates, set
  `subject_node_type/id` + `object_node_type/id` on each item, and key on a
  node-aware `canonical_relationship_key` (`mode|subjType:subjId|objType:objId`)
  that matches what `build_relationship_graph` already indexes.
- Populate `subject_search_label` / `object_search_label` on **every** item in
  both modes (crop search term, or group first alias). Keep emitting
  `subject_crop_label` in crop mode for back-compat, but make the label field the
  one downstream consumers read.
- Default aggregate runs to the principle-bearing tiers.

### Step 4 — Carry node identity through discovery
File: `src/crop_search_framework/relationships.py`

- `discover_relationships` and the row builder forward `node_mode` and stamp
  `subject_node_type/id` + `object_node_type/id` (and `node_mode`,
  `subject_search_label` / `object_search_label`) on every ledger row, alongside
  the existing pair fields. Dedup keys use the node-aware canonical key for
  aggregate runs.
- **Connector arg (caution 2):** change the connector call from
  `crop=item["subject_crop_label"].lower()` to the mode-agnostic
  `crop=item["subject_search_label"].lower()` so aggregate items pass a group
  term (e.g. "legume") instead of crashing on a missing crop label.

### Step 4b — Fetch-queue selection (caution 1)
File: `src/crop_search_framework/relationship_pipeline.py`

`select_relationship_fetch()` is its own stage between discovery and fetch and
currently assumes crop ids on every row. Update it to:

- Read crop ids via `row.get("subject_crop_id", "")` / `.get("object_crop_id")`
  instead of hard `[]` access.
- Copy `subject_node_type/id` + `object_node_type/id` + `node_mode` onto the
  candidate and queue rows so node identity survives into the fetch captures.
- Confirm `_prefilter` / `_queue_row` and the per-(pair×mode) balancing key off
  `relationship_source_key` / `canonical_relationship_key` only (both already
  node-aware), never off crop ids.

### Step 5 — Fetch/corpus/extraction context
Files: `relationship_pipeline.py`, `schemas/raw-capture.schema.json`,
`prompts/relationships/extract-opus.md`

- Thread the node fields onto relationship raw captures and corpus
  `relationship_hits` (raw-capture schema already has optional relationship
  context fields; extend with node fields).
- Update the extraction prompt with explicit aggregate guidance + a few-shot:
  a group-level statement → aggregate claim (`subject_node_type=functional_group`
  …); a crop-specific statement inside an aggregate-targeted doc → still a direct
  crop claim. Reinforce the no-fabrication rule.

### Step 6 — CLI
File: `src/crop_search_framework/cli.py`

Add `--node-mode {crop,aggregate}` to `plan-relationship-queries` and
`discover-relationships` (default `crop`), threading through kwargs, summaries,
and saved plans. No new top-level verbs — `build-relationship-graph` /
`resolve-crop-relationship` already consume whatever claims exist.

### Step 7 — Tests
File: `tests/test_relationship_aggregate_discovery.py` (new)

- Aggregate pair counts (6 functional groups → expected ordered/unordered counts).
- Aggregate query text contains group terms ("legume", "cereal", "brassica
  family") and **no** specific crop id.
- Aggregate query items carry `subject_search_label` / `object_search_label`
  (group terms), and crop-mode items still carry crop labels — guards the
  connector-arg caution.
- Discovery rows carry `subject_node_type/id` + `object_node_type/id`.
- **Fetch-queue survival (caution 1):** feed an aggregate discovery ledger (rows
  with node fields and no crop ids) to `select_relationship_fetch()` and assert
  it queues without `KeyError` and the queued rows retain
  `subject_node_type/id` + `object_node_type/id`.
- End-to-end production→consumption: write an aggregate `functional_group`
  `cereal ← legume` claim into a claims dir, `build_relationship_graph`,
  then `resolve_crop_relationship("wheat","soybean")` →
  `inferred_from_group` / `functional_group`. (Closes the loop the resolver unit
  tests assert in isolation today.)

## Sequencing & validation

| Step | Touches | Gate |
|------|---------|------|
| 1 | query-plan schema | item validates with node fields |
| 2 | vocabulary | aggregate templates load |
| 3 | planner | aggregate plan renders group queries + `*_search_label` |
| 4 | discovery | rows carry node identity; connector uses `*_search_label` |
| 4b | fetch-queue (`select_relationship_fetch`) | aggregate rows queue without `KeyError`, node ids preserved |
| 5 | capture/corpus/prompt | extraction context complete |
| 6 | CLI | `--node-mode aggregate` runs |
| 7 | tests | counts, rendering, queue survival, end-to-end inference |

Run `.venv/bin/python -m unittest discover -s tests` after each step. Live
aggregate crawl + in-session Opus extraction remain manual gates; this plan
builds the contracts/planner/discovery/prompt so an aggregate slice can actually
populate the inference lane.

## Out of scope / follow-ups

- Genus-level aggregate queries (catalog has genus nodes; lower priority than
  family/functional-group).
- Auto-deriving aggregate claims by rolling up multiple direct crop claims
  (e.g. several legume→cereal pairs → a `functional_group` summary). Tempting but
  separate; it is inference-over-evidence, not extraction, and needs its own
  provenance rules.
