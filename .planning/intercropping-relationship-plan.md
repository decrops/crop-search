# Intercropping Relationship Capture — Plan (rev 2)

> **Status: IMPLEMENTED (2026-06-29).** All steps shipped; suite green at 160
> tests incl. `tests/test_relationship_intercrop.py`. Symmetric resolution and
> reverse-keyed matrix mirroring verified; intercrop discovery live-smoked
> (auto → unordered). See the `2026-06-29: Intercropping / symmetric relationship
> capture` entry in [docs/IMPLEMENTATION_LOG.md](../docs/IMPLEMENTATION_LOG.md).

## Current state

Intercropping has a **labeled drawer but no contents**, plus two correctness
gaps that would make the drawer unreliable even once filled.

- The vocabulary defines `intercrop`, `relay_crop`, `strip_crop`, `mixed_crop`,
  and `companion_crop`
  ([relationship-vocabulary.json](../config/relationships/relationship-vocabulary.json)).
  The dense matrix has per-mode cells for all of them.
- `default_modes` is `["rotation"]`, so every run to date is rotation-only:
  10 rotation claims, 0 intercrop; all intercrop matrix cells `not_searched`.
  Intercropping wording appears only incidentally inside *parameter* captures.
- The claim schema already fits intercrop evidence: `context.arrangement`,
  `row_ratio`, `plant_density_adjustment`, `temporal_offset`, and numeric/range
  `value` (land-equivalent ratio). **No claim-schema change is needed.**

### Mode directionality (this drives the whole plan)

| Directional | Symmetric |
|-------------|-----------|
| rotation, continuous_cropping, double_crop, **relay_crop**, cover_crop | **intercrop, strip_crop, mixed_crop, companion_crop** |

Only the four symmetric modes get endpoint-order canonicalization / mirroring.
`relay_crop` is **directional** and must NOT be mirrored.

### Aggregate-template coverage today (correction)

Aggregate (`--node-mode aggregate`) group-level templates currently exist for
`rotation`, `continuous_cropping`, `intercrop`, `companion_crop`, `cover_crop`.
They do **not** exist for `strip_crop` or `mixed_crop` (both symmetric) or
`relay_crop`/`double_crop`. So aggregate intercropping inference works for
`intercrop` and `companion_crop` only — not the whole symmetric family yet.

## The two correctness gaps

**Gap A — graph/resolver use a directed key.** `build_relationship_graph`
indexes, and `resolve_crop_relationship` looks up, direct/aggregate evidence by
`mode|subject|object`
([relationship_pipeline.py](../src/crop_search_framework/relationship_pipeline.py)).
For a symmetric mode an `intercrop corn|soybean` claim is invisible to
`resolve("soybean","corn")`.

**Gap B — matrix population trusts the claim's key string.**
`populate_relationship_matrix` buckets claims by `claim["canonical_relationship_key"]`
verbatim and matches it to the cell's key, which is the **sorted**
`canonical_relationship_key(mode,…)`
([relationships.py](../src/crop_search_framework/relationships.py)). If Opus emits
`intercrop|soybean|corn` instead of the sorted `intercrop|corn|soybean`, the
claim never lands in either cell. **Prompt guidance alone is too fragile here —
this must be enforced in code.**

## Design decisions

- **One canonicalization rule, enforced in code.** For symmetric modes the
  canonical endpoint order is `sorted(a, b)`. A single helper
  `canonicalize_endpoints(directionality, a, b)` returns the ordered pair
  (sorted for symmetric, as-is for directional). It is applied to claims at load
  time and used by both the matrix and the graph, so correctness never depends on
  what Opus emitted. Directionality comes from the vocabulary (authoritative),
  not the claim's free-text `direction` field.
- **Resolver normalizes the query too.** `resolve_crop_relationship` sorts its
  `(subject, object)` for symmetric modes before lookup, so `(a,b)` and `(b,a)`
  hit the same index entry. The graph carries a small `mode_directionality` map
  so the resolver needs no extra config load.
- **`--pair-mode auto` becomes the default.** `auto` resolves to `unordered` when
  every selected mode is symmetric, else `ordered`. This makes the symmetric =
  unordered behavior a real default, not just a recommended command posture, while
  keeping mixed/directional runs ordered. Explicit `--pair-mode ordered|unordered`
  still overrides.
- **No claim-schema change.** Intercrop specifics ride existing fields.

## Steps

### Step 1 — Canonicalization helper + symmetric-claim normalization (fixes Gap B foundation)
Files: `src/crop_search_framework/relationships.py`,
`src/crop_search_framework/relationship_pipeline.py`

- Add `canonicalize_endpoints(directionality, a, b)` (sorted for `symmetric`,
  identity for `directional`) next to `canonical_relationship_key`.
- Add a normalization pass applied to validated claims (in
  `validate_relationship_claims` or a thin wrapper both consumers call): look up
  the claim's mode directionality; for symmetric modes, reorder the claim's
  endpoints to canonical order and **recompute** `canonical_relationship_key`,
  `ordered_pair_key`, and the subject/object node tuple accordingly. Directional
  claims pass through untouched. Now the matrix and graph both see a consistent,
  order-independent key regardless of Opus output.

### Step 2 — Matrix population uses the canonical key (closes Gap B)
File: `relationship_pipeline.py`

- `populate_relationship_matrix` buckets normalized claims (Step 1), so a symmetric
  claim mirrors into both `corn|soybean` and `soybean|corn` cells.
- Test: one `intercrop corn|soybean` claim **and** one emitted reverse as
  `intercrop|soybean|corn` each mark **both** ordered cells `evidence_found`.

### Step 3 — Symmetric-aware graph + resolver (closes Gap A)
File: `relationship_pipeline.py`

- `build_relationship_graph`: load mode directionality; index direct/aggregate
  evidence under canonical (sorted-for-symmetric) endpoints; persist a
  `mode_directionality` map in the graph JSON. Host overlays already key on a
  single shared host group — unchanged.
- `resolve_crop_relationship`: for symmetric modes, sort `(subject, object)`
  (and each aggregate-candidate pair) before lookup.
- Test: an `intercrop corn|soybean` claim resolves `direct_evidence` for both
  `(corn,soybean)` and `(soybean,corn)`; a `rotation` claim stays one-directional.

### Step 4 — Intercrop extraction contract
File: `prompts/relationships/extract-opus.md`

- **Subtypes:** `intercrop_compatibility`, `land_equivalent_ratio`,
  `row_arrangement`.
- **Single effect label (the schema's `effect` is one string):** choose
  `beneficial` for a **measured** advantage (e.g. LER > 1 or a reported yield
  gain), `compatible` for non-quantified stated compatibility, `neutral` for no
  effect, `incompatible`/`avoid` for measured disadvantage or strong competition,
  `conditional` when it depends on arrangement/density/region. Pick the single
  best-supported label — do not stack two.
- **Capture intercrop context:** `arrangement`, `row_ratio`,
  `plant_density_adjustment`, `temporal_offset`; numeric/range LER in `value`.
- **Direction + keys:** set `direction: simultaneous` (or `bidirectional`) for
  intercrop; emit sorted `canonical_relationship_key`/`ordered_pair_key` for
  symmetric modes. (Code now enforces this per Step 1, so the prompt note is a
  best-effort convention, not the correctness guarantee.)
- **Routing rule:** an explicit crop-pair statement ("maize–bean intercropping
  raised LER to 1.3") is a relationship claim; generic "consider intercropping"
  advice with no named partner stays parameter/management text.

### Step 5 — Aggregate templates for the rest of the symmetric family
File: `config/relationships/relationship-vocabulary.json`

- Add `aggregate_query_templates` to `strip_crop` and `mixed_crop` (both
  symmetric) so `--node-mode aggregate` covers the symmetric intercropping family,
  matching the existing `intercrop`/`companion_crop` coverage. `relay_crop` is
  directional and out of this step.

### Step 6 — Mode-aware `--pair-mode auto` default
Files: `src/crop_search_framework/relationships.py`,
`src/crop_search_framework/cli.py`

- Accept `pair_mode="auto"` in `build_relationship_query_plan` /
  `discover_relationships`: resolve to `unordered` iff every selected mode is
  symmetric, else `ordered`. Record the resolved value in the plan payload.
- CLI: change `--pair-mode` choices to `{auto,ordered,unordered}`, default
  `auto`, on both `plan-relationship-queries` and `discover-relationships`.
- Test: `--mode intercrop` with `auto` plans unordered; `--mode rotation` plans
  ordered; mixed selection plans ordered.

### Step 7 — Run the lane + cross-lane guard + eval/gold
Files: `README.md`, `relationship_pipeline.py`, `tests/golden/relationships/`

- **Recipe (now default-correct):** `discover-relationships intercrop-001 --mode
  intercrop` (auto → unordered) → select → fetch → corpus → in-session Opus
  extraction (Step 4) → validate → populate → build-graph →
  `resolve-crop-relationship --mode intercrop`. Do **not** add `intercrop` to
  `default_modes`; keep discovery cost opt-in.
- **Cross-lane guard:** generalize `relationship_parameter_span_conflicts` to
  accept a set of parameter ids (the manifest has no intercropping parameter
  today, so this is forward-looking; the Step 4 routing rule is the live
  boundary).
- **Eval/gold:** add an `intercrop` gold pair (e.g. `corn|soybean` →
  `beneficial`, LER rationale) so `eval_relationships` scores the intercrop lane.

### Step 8 — Tests
File: `tests/test_relationship_intercrop.py` (new)

- Symmetric resolve: both orderings, both for direct and aggregate evidence.
- Matrix mirroring from a correctly-keyed claim **and** from a reverse-keyed
  claim (the Gap B regression guard).
- `pair_mode="auto"` resolution per selected-mode directionality.
- `relay_crop` (directional) is **not** mirrored — a `relay_crop a|b` claim does
  not answer `(b,a)`.
- Span-conflict guard accepts a supplied intercrop parameter id.

## Sequencing & validation

| Step | Touches | Gate |
|------|---------|------|
| 1 | canonicalization helper + normalization | symmetric claims normalized regardless of emitted key |
| 2 | matrix population | reverse-keyed symmetric claim still mirrors both cells |
| 3 | graph + resolver | symmetric claim resolves both orderings |
| 4 | extract prompt | intercrop contract + single-effect rule |
| 5 | vocabulary | strip_crop/mixed_crop aggregate templates |
| 6 | planner + CLI | `--pair-mode auto` default works |
| 7 | README/guard/gold | intercrop recipe runnable; eval covers it |
| 8 | tests | all of the above green, incl. relay_crop-not-mirrored |

Run `.venv/bin/python -m unittest discover -s tests` after each step. Live
intercrop fetch + in-session Opus extraction + human acceptance remain the same
manual gates as the rest of the relationship lane.

## Out of scope / follow-ups

- `relay_crop` is **directional** — it inherits none of the symmetric
  mirroring; deeper relay/strip/mixed extraction nuance (subtypes, effects) is a
  later prompt iteration. Only `intercrop`, `strip_crop`, `mixed_crop`, and
  `companion_crop` are mirrored.
- Quantitative LER aggregation/scoring across sources (LER is captured as
  evidence value now; numeric synthesis is a separate layer).
- A dedicated `management.intercropping_compatibility` parameter — add only if a
  non-relationship home for generic intercropping advice is actually needed.
