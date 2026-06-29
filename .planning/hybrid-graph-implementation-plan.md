# Hybrid Relationship Evidence Graph — Implementation Plan (rev 2)

> **Status: IMPLEMENTED (2026-06-29).** All steps below shipped; the suite is
> green at 11 tests (the 8 originals + the rejected-claim guard + cross-group and
> family-priority inference tests). See the `2026-06-29: Hybrid relationship
> evidence graph` entry in [docs/IMPLEMENTATION_LOG.md](../docs/IMPLEMENTATION_LOG.md).

Goal: turn the failing TDD suite
[`tests/test_hybrid_relationship_graph.py`](../tests/test_hybrid_relationship_graph.py)
green (currently 8 tests → 9 errors + 1 failure), add the resolver/graph/dedup
machinery the hybrid plan calls for, and wire the CLI — **without** breaking the
existing crop-pair matrix lane or its old-style crop-only claims.

### Test command (deps matter)

```
.venv/bin/python -m unittest tests.test_hybrid_relationship_graph
```

Use `.venv/bin/python` explicitly: the system `python3` lacks `jsonschema`, so
`PYTHONPATH=src python3 -m unittest ...` fails early on import, not on logic. If
the venv is stale, `pip install -e .` (or install `jsonschema`) first.

This revision incorporates review findings; each is called out inline as
**[R-High]** / **[R-Med]** / **[Q]** at the point it changes the design.

---

## Design decision up front: how unordered mode handles directional searches

**[R-High] Unordered planning must not silently drop reverse-direction search
intent.** `rotation` and 5 of the 9 vocabulary modes are `directional` with
"{subject} after {object}" templates
([relationship-vocabulary.json](../config/relationships/relationship-vocabulary.json)).
Emitting only `i <= j` ordered pairs would lose the "B after A" cell.

The discovery fixture in the test already encodes the intended contract — one
neutral search row carrying both candidate orderings:

```json
"pair_mode": "unordered",
"search_pair_key": "rotation|cabbage|pak_choi",
"candidate_ordered_pair_keys": ["cabbage|pak_choi", "pak_choi|cabbage"]
```

So **unordered mode collapses both directions into one search but preserves both
ordered targets**, and extraction later assigns evidence to whichever ordered
cell the claim's `direction` implies. Chosen approach (over "restrict unordered
to symmetric modes only", which would leave directional modes stuck on the
expensive `n×n` plan):

- An unordered pair `{A, B}` produces **one** query per (mode, subtype, tier),
  rendered from a **direction-neutral subtype** where the mode has one
  (e.g. rotation's `rotation_interval` / `crop sequence` template), falling back
  to a neutral join of the two crop names when a mode has only directional
  templates. This keeps `query_count == planned_pair_count` for the test's
  `queries_per_pair=1` case.
- Each query/row carries `pair_mode`, `search_pair_key` (canonical unordered key
  `mode|min|max`), and `candidate_ordered_pair_keys` (both orderings; one entry
  for self-pairs).
- `candidate_ordered_pair_keys` is threaded **all the way through**
  discovery → fetch capture → extraction prompt context, so a directional claim
  extracted from a neutral source still lands in the correct ordered matrix cell
  via its own `direction` + `ordered_pair_key`.

This is a real contract change across `relationships.py` (planner + discovery
row builder) — not just a counter. It is scoped in Steps 2 and 2b below.

---

## Step 1 — Tighten, don't just relax, the relationship-claim schema (2 tests)

File: [`schemas/crop-relationship-claim.schema.json`](../schemas/crop-relationship-claim.schema.json)

Why first: `validate_relationship_claims()` *silently drops* schema-invalid
claims, so today's `not_searched != evidence_found` matrix failure is a
downstream symptom — the node-bearing claims are being rejected. Fixing the
schema fixes both `test_crop_claims_remain_valid_with_and_without_node_fields`
and the matrix test (no populate-code change).

**[R-Med] Do not blanket-remove crop fields from `required`** — that would let a
claim with *no* usable subject/object identifier validate. Use conditional
requirements so each side has a valid identity:

- Add optional properties `subject_node_type`, `object_node_type`
  (enum `["crop","genus","botanical_family","functional_group","host_group"]`)
  and `subject_node_id`, `object_node_id` (string).
- Keep `subject_crop_id` / `object_crop_id` / `*_crop_group` as defined optional
  properties, but **remove them from the unconditional `required` list** and
  re-impose identity via `allOf` of two `anyOf` blocks:
  - subject side: `anyOf: [ {required:[subject_crop_id, subject_crop_group]},
    {required:[subject_node_type, subject_node_id]} ]`
  - object side: same for object fields.
- Keep `additionalProperties: false`.

Result: old-style crop-only claims valid; new-style node claims valid;
aggregate (family/host_group) claims valid; an identity-less claim is rejected.

Verifies: `test_crop_claims_remain_valid_with_and_without_node_fields`,
`test_ordered_matrix_population_accepts_claims_from_unordered_search_context`.

## Step 2 — `pair_mode` on the query planner (1 test)

Files: [`src/crop_search_framework/relationships.py`](../src/crop_search_framework/relationships.py),
[`schemas/crop-relationship-query-plan.schema.json`](../schemas/crop-relationship-query-plan.schema.json)

- Add `unordered_crop_pairs(crops, include_self_pairs=True)` → each pair once
  (`index_i <= index_j`), yielding `n(n+1)/2` with self-pairs (7→28, 25→325,
  120→7260, matching the test).
- Add `pair_mode: str = "ordered"` to `build_relationship_query_plan()`. When
  `"unordered"`, build pairs via `unordered_crop_pairs` and render the
  direction-neutral query described above; emit `search_pair_key` +
  `candidate_ordered_pair_keys` per query item.
- Add `"pair_mode": pair_mode` to the payload and to the query-plan schema
  (enum `["ordered","unordered"]`; the schema is `additionalProperties:false`,
  so the key must be declared or validation inside the function fails).
- **Declare the new item-level fields in the schema too.** `$defs.query_item`
  is *also* `additionalProperties:false`, so `RelationshipQueryPlanItem.to_json()`
  emitting `search_pair_key` / `candidate_ordered_pair_keys` (and a per-item
  `pair_mode`, if carried) would fail validation unless they are added as
  optional properties on `query_item`: `pair_mode` (enum),
  `search_pair_key` (string), `candidate_ordered_pair_keys`
  (array of strings, `minItems:1`). Leave them optional so `ordered`-mode items
  validate unchanged.

Verifies: `test_unordered_pair_counts_for_planning_sizes`.

## Step 2b — Thread `pair_mode` through discovery (no new test, contract glue)

File: `relationships.py`

- `discover_relationships()` gains `pair_mode` and forwards it to
  `build_relationship_query_plan()`.
- Discovery rows carry `pair_mode`, `search_pair_key`,
  `candidate_ordered_pair_keys` (the planner already produced them); confirm
  `relationship_discovery_summary()` and the saved plan reflect `pair_mode`.
- Dedup key for unordered runs is `search_pair_key` + source (so one source can
  back both ordered cells). This matches the existing fixture row shape.

## Step 3 — `load_node_catalog()` (1 test)

File: [`relationship_pipeline.py`](../src/crop_search_framework/relationship_pipeline.py)

- `load_node_catalog(repo_root)` reads
  `config/relationships/node-catalog.json` and returns the parsed dict (the test
  does its own `SchemaRegistry` validation). The catalog already contains every
  node the resolver needs — `pak_choi`, `cabbage` (both `brassicaceae` +
  `clubroot_host`), family/functional/host nodes — **no catalog edits required**.

Verifies: `test_node_catalog_is_valid`.

## Step 4 — Evidence graph + resolver (3 existing + 1 new test)

File: `relationship_pipeline.py`

### 4a. Claim → node-tuple normalization adapter **[R-High]**

Existing fixtures/artifacts use only `subject_crop_id`/`object_crop_id`
([test_relationships.py](../tests/test_relationships.py)). The graph must index
old-style claims, not just node-bearing ones. Add a normalizer that, per side,
yields `(node_type, node_id)`:

- if `*_node_type`/`*_node_id` present → use them;
- else if `*_crop_id` present → synthesize `("crop", crop_id)`.

All graph indexing goes through this adapter so both claim styles coexist.

### 4b. Status filter **[R-High]**

`validate_*` means schema-valid, **not** accepted. `rejected` and `conflict` are
schema-valid statuses. The graph builder must filter to evidence-bearing claims
only — reuse matrix population's policy: keep `status in {accepted,
needs_review}`, drop `rejected`/`conflict`. The resolver therefore never returns
rejected evidence.

> New test to add to the suite:
> `test_resolver_excludes_rejected_claims` — a `pak_choi↔cabbage` claim with
> `status:"rejected"` must resolve to `status:"no_evidence"`, not
> `direct_evidence`.

### 4c. `build_relationship_graph(repo_root, run_id)`

- Load claims via `validate_relationship_claims`, apply 4b filter, map each side
  through 4a, partition into lanes: **direct** (crop↔crop), **aggregate**
  (`botanical_family` / `functional_group` / `genus`), **host_group** overlays.
- **Key every graph index by `relationship_mode` as well as the node tuple**
  — i.e. index on `(relationship_mode, subject_node_tuple, object_node_tuple)`,
  not the node tuple alone. Otherwise `rotation` evidence could bleed into
  `intercrop` or any other mode for the same crop pair. The resolver (4d) then
  only ever reads the lane for its requested `mode`. Host-group overlays are
  matched within the same mode as well.
- Persist to `exploration/relationships/graph/<run_id>.json` (tests build then
  resolve in separate calls).

### 4d. `resolve_crop_relationship(repo_root, run_id, subject, object, mode="rotation")` **[Q]**

**Open question resolved:** the test calls with only subject/object and all
fixtures are rotation, so **default `mode="rotation"`** and expose `--mode` on
the CLI. Returning per-mode results is deferred (noted as follow-up); a single
default mode satisfies the suite and the vocabulary's directional semantics.

Resolution order:

1. **Alias resolution** — map each input string to a catalog node by
   case-insensitive alias. Unresolved input → `status:"no_evidence"`,
   `unknown_nodes:[<raw input>]`.
2. **Direct lane** — crop↔crop claim for the resolved ordered pair →
   `status:"direct_evidence"`, `primary_effect` from the claim.
3. **Host-risk overlay** — for any `host_group` claim whose group is shared by
   *both* crops' `host_groups`, append `caveats[].host_group` and add
   `"host_risk_caveat"` to `status_flags`. Overlay rides on top of direct
   evidence; it does not change `status`.
4. **Group inference (when no direct evidence)** **[R-Med]** — do **not**
   restrict to *shared* aggregates. Form the subject's aggregate candidates
   (`botanical_family`, `functional_group`, `genus`) and the object's aggregate
   candidates, then look for a directional aggregate claim
   `subject_aggregate → object_aggregate` for the mode. This catches both
   same-family avoidance (brassicaceae→brassicaceae) **and** cross-group
   sequences (cereal-after-legume: `functional_group cereal` ←
   `functional_group legume`). On a hit: `status:"inferred_from_group"`,
   `inference_basis` = the matched aggregate type (e.g. `"botanical_family"`),
   `primary_effect` from the claim. Priority order: family → functional_group →
   genus; respect relationship direction throughout.

Return shape (union across tests): `status`, `primary_effect`,
`inference_basis`, `status_flags` (list), `caveats` (list), `unknown_nodes`
(list).

Verifies: `test_host_risk_caveat_overlays_direct_beneficial_evidence`,
`test_resolver_infers_minor_crops_from_family_claim`,
`test_resolver_returns_no_evidence_for_unknown_pair`, plus the new
`test_resolver_excludes_rejected_claims`.

## Step 5 — Cross-lane span dedup (1 test)

File: `relationship_pipeline.py`

- `relationship_parameter_span_conflicts(repo_root, run_id, param_run)`:
  collect relationship-claim `evidence_text` spans for `run_id`; read normalized
  param claims from `exploration/normalized/<param_run>/*.json`; for each with
  `parameter_id == "management.rotation_recommendation"`, compare its
  `provenance.evidence_text` against the spans. Return
  `{conflict_count, conflicts:[{parameter_claim_id, ...}]}`.

Verifies: `test_routing_rule_detects_duplicate_relationship_parameter_spans`.

## Step 6 — CLI wiring (update existing, add only the new) **[R-Med]**

File: [`src/crop_search_framework/cli.py`](../src/crop_search_framework/cli.py)

- **Update** the existing `plan-relationship-queries`
  ([cli.py:549](../src/crop_search_framework/cli.py)) and `discover-relationships`
  ([cli.py:620](../src/crop_search_framework/cli.py)) parsers: add
  `--pair-mode {ordered,unordered}` (default `ordered`) and pass it through to
  `build_relationship_query_plan()` / `discover_relationships()`, into their
  summaries and the saved query plan. Do **not** add duplicate parsers.
- **Add** two new parsers:
  - `build-relationship-graph <run_id>`
  - `resolve-crop-relationship <run_id> --subject <crop_or_alias>
    --object <crop_or_alias> [--mode rotation]`

---

## Sequencing & checkpoints

| Step | Touches | Suite state after |
|------|---------|-------------------|
| 1 | claim schema (conditional identity) | 2 / 8 |
| 2 | planner `pair_mode` + query-plan schema | 3 / 8 |
| 2b | discovery threading | 3 / 8 (no new test; guards R-High) |
| 3 | `load_node_catalog` | 4 / 8 |
| 4 | adapter + status filter + graph + resolver (+1 new test) | 8 / 9 |
| 5 | span dedup | 9 / 9 |
| 6 | CLI (update + add) | green |

After Step 4 the suite has **9** tests (the added rejected-claim test). Run
`.venv/bin/python -m unittest tests.test_hybrid_relationship_graph` after each
step. All changes are pure library/schema work behind the manual crawl /
in-session Opus extraction gates from the parent
[hybrid-relationship-evidence-graph-plan.md](hybrid-relationship-evidence-graph-plan.md);
no live crawl or model spend is triggered.

## Residual follow-ups (out of scope for green suite)

- Per-mode resolver output (return results across all 9 vocabulary modes) — **[Q]**.
- Directional-evidence assignment from neutral unordered sources is specified in
  Step 2b but only exercised by counts in the current suite; add an
  extraction-level test when the Opus extraction lane is built.
