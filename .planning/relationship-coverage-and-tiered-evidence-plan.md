# Relationship Coverage + Tiered Evidence Plan (rev. 2)

**Status:** PROPOSED — revised after review round 1
**Date:** 2026-06-30
**Goal:** Reach near-complete rotation **and** intercropping coverage across all crops, where every
relationship is first backed by a **textbook/reference** claim and **upgraded to peer-reviewed
evidence wherever a real paper exists** — with the upgrade tracked and measurable.

**Changelog vs rev. 1:** connector routing for `reference_encyclopedia` added and decoupled from the
default discovery order (High-1); aggregate scope corrected to real planner counts + a new
`--aggregate-node-type` filter (High-2); merged multi-run graph semantics specified (High-3);
`source_tier_id` enforced in validation (Med-1); full effect-polarity table defined (Med-2);
review-status separated from grade in coverage (Med-3); plan links fixed to `../` (Low).

---

## 1. Why this plan exists

Two requests, one plan:

1. **Coverage gaps.** Direct matrix coverage is sparse: rotation **14/55** crop pairs (25%),
   intercrop **1/55** (2%, only `corn|soybean`). The user wants *all crops related to each other*
   for rotation **and** intercropping.
2. **Evidence methodology.** "Use textbook references as the basis for relationship claims, and
   replace them with peer-reviewed evidence wherever possible."

### The blocking finding (still the core)

The fetch/discovery side is already tier-aware — `select_relationship_fetch`
([relationship_pipeline.py:88-148](../src/crop_search_framework/relationship_pipeline.py#L88-L148))
ranks candidates by `tier_trust`. **But the evidence layer is tier-blind:**

- `populate_relationship_matrix` ([relationship_pipeline.py:597-607](../src/crop_search_framework/relationship_pipeline.py#L597-L607))
  collapses claims with a plain `Counter(effects).most_common(1)` vote — a Wikipedia claim and a
  peer-reviewed claim count **equally**.
- `_summary_effect` ([relationship_pipeline.py:737-741](../src/crop_search_framework/relationship_pipeline.py#L737-L741)),
  used by the resolver, does the same.
- A cell records no tier, so "which cells are textbook-only and need a peer-reviewed upgrade?" is
  unanswerable.
- `config/source-tiers/default.json` ranks tiers via `priority`/`trust_weight`, but **nothing
  downstream reads them**, and the `reference_encyclopedia` tier 3 live claims use is **undefined**.

Part A fixes this in code; Parts B–C do the coverage work on top of it. Current inventory: **22
claims** — 19 `peer_reviewed_science`, 3 `reference_encyclopedia`, **0 `textbook_reference`**
(20 rotation, 2 intercrop).

---

## 2. Strategy: a functional-group backbone the resolver expands to every pair

Hand-extracting 55 pairs × 2 modes of direct crop-pair claims is intractable. The resolver already
infers a crop pair from a **functional-group / family** claim
([relationship_pipeline.py:861-878](../src/crop_search_framework/relationship_pipeline.py#L861-L878)),
so a small group-level backbone covers every pair.

The 11 crops fall into **6 functional groups**: `cereal` {wheat, barley, corn, rice},
`oilseed` {rapeseed, sunflower}, `legume` {soybean}, `root_crop` {sugar_beet, potato},
`fiber` {cotton}, `fruiting_vegetable` {tomato}.

> **Corrected scope (High-2).** The aggregate planner does **not** default to functional groups
> only. `load_aggregate_nodes` defaults to **all three** aggregate types
> ([relationships.py:312-333](../src/crop_search_framework/relationships.py#L312-L333)) — the catalog
> has **6 botanical-family + 6 functional-group + 2 host-group** nodes — and `aggregate_node_pairs`
> pairs within each type ([relationships.py:336-360](../src/crop_search_framework/relationships.py#L336-L360)).
> A verified default run plans **74 ordered pairs** (rotation) / **44 unordered** (intercrop) →
> ~148 queries/tier for rotation alone. To get the lean backbone we must **scope to one node type**
> (new `--aggregate-node-type` filter, A6).

**Scoped to `functional_group`:** rotation (directional, ordered incl. self) = `6·6 = 36`;
intercrop (symmetric, unordered incl. self) = `6·7/2 = 21` → **57 group claims** give the resolver
100% answerable coverage of all 55 pairs in both modes. `host_group` adds 2 self-pairs for risk
overlays; `botanical_family` (another 36+21) is optional phase-2 precision.

Two senses of "coverage", both reported:

| Sense | Definition | Backbone target | Today |
|---|---|---|---|
| **Answerable coverage** | pairs the resolver answers (incl. group inference) | 55/55 both modes | rotation high, intercrop ~4 |
| **Direct matrix coverage** | crop-pair cells with their own claim | grows via upgrades | 14/55 rot, 1/55 inter |
| **Evidence grade** | best tier backing the answer | peer-reviewed where possible | 19 PR / 3 ref |

---

## 3. Evidence tiers: two bands

- **Backbone band** (the "basis"): `textbook_reference` → `international_institution` →
  `extension_publication` → `industry_grower_guide` → `reference_encyclopedia`.
- **Evidence band** (the "upgrade"): `peer_reviewed_science`.

A cell/answer's **evidence_grade** is `peer_reviewed` if any peer-reviewed claim backs it, else
`reference_backbone` if any backbone claim does, else `none`. **Upgrade candidates** = covered cells
whose grade is `reference_backbone`.

> **Honesty note.** True textbook full text is often paywalled (`access_policy:
> metadata_or_open_text_only`). In practice the backbone leans on `international_institution`
> (FAO/CGIAR), `extension_publication` (.edu/.gov), and `reference_encyclopedia` (Wikipedia), with
> `textbook_reference` where open previews exist. **Decision D2** lets the user narrow this.

---

## Part A — Make the evidence layer tier-aware (code)

### A1. Define the full tier ranking (decoupled from discovery order) — High-1
- Add `reference_encyclopedia` to the `tiers` list in `config/source-tiers/default.json`
  (`priority: 6`, `trust_weight: 0.4`, `evidence_role: "Encyclopedic overview; broad coverage,
  lowest credibility."`). This is what lets the 3 live Wikipedia claims **rank** correctly.
- **Do NOT add it to the `comprehensive_accessible` `tier_order`.** The discovery loop reads each
  query item's `source_tier_id` and calls `connector_results_for_tier`
  ([relationships.py:764](../src/crop_search_framework/relationships.py#L764)); an unknown/unrouted
  tier returns `[], []` ([discovery_connectors.py:96](../src/crop_search_framework/dev_tools/discovery_connectors.py#L96))
  → a **dead discovery tier**. Keeping it out of the default order means it is never silently
  planned; it stays opt-in via `--source-tier-id reference_encyclopedia`.

### A2. Add the `reference_encyclopedia` connector branch — High-1
- In `connector_results_for_tier`
  ([discovery_connectors.py:43-96](../src/crop_search_framework/dev_tools/discovery_connectors.py#L43-L96))
  add a `reference_encyclopedia` branch routing to `wikipedia_results` only (the provider already
  exists; it backs the international/extension/industry tiers). Now the tier is usable when
  explicitly requested, not dead.
- Tests: branch returns Wikipedia provider results; unknown tier still returns `([], [])`.

### A3. Tier ranking helpers
- `src/crop_search_framework/source_tiers.py`: add
  `tier_rank_index(repo_root, manifest_path=...) -> Dict[str, int]` (`tier_id → priority`, lower =
  better; unknown → worst+1) and `tier_band(tier_id) -> "evidence" | "backbone"`.

### A4. Effect-polarity table + tier-weighted resolution — Med-2
Define one polarity map over the full effect enum
([claim schema:89](../schemas/crop-relationship-claim.schema.json#L89)):

| effect | polarity class |
|---|---|
| `beneficial`, `compatible` | **positive** |
| `incompatible`, `avoid` | **negative** |
| `conditional` | **conditional** (context-dependent) |
| `neutral` | **neutral** |
| `unknown` | **ignore** |

`tiered_effect(claims, rank_index)` (new helper, used by both populate and resolve):
1. Keep only top-tier claims (min `priority`); compute their polarity classes.
2. **Hard conflict:** positive **and** negative both present in the top tier → `status =
   conflicting_evidence`, `summary_effect = conflicting`.
3. **Ambiguity (not buried):** if `conditional` co-occurs with any decisive polarity →
   `summary_effect = conditional`, set flag `ambiguous_effect = true` (the honest "it depends").
4. Otherwise `summary_effect` = plurality of decisive effects; if only `neutral` → `neutral`; if
   only `unknown`/empty → `unknown`.
5. Lower tiers never change the effect, but if a lower tier's decisive polarity disagrees with the
   top tier → `tier_superseded_conflict = true` (visible, not silent).
- Returns `{summary_effect, status, best_source_tier, evidence_grade, tier_histogram,
  ambiguous_effect, tier_superseded_conflict}`.

### A5. Apply in matrix + resolver
- `populate_relationship_matrix`
  ([relationship_pipeline.py:592-608](../src/crop_search_framework/relationship_pipeline.py#L592-L608)):
  replace the inline vote with `tiered_effect`; write new cell fields `best_source_tier`,
  `tier_histogram`, `evidence_grade`, `ambiguous_effect`, `tier_superseded_conflict`. Update
  `schemas/crop-relationship-matrix.schema.json`.
- `resolve_crop_relationship`
  ([relationship_pipeline.py:856-878](../src/crop_search_framework/relationship_pipeline.py#L856-L878)):
  surface `best_source_tier`, `evidence_grade`, `ambiguous_effect`, `tier_superseded_conflict` on
  both the `direct_evidence` and `inferred_from_group` branches.

### A6. `--aggregate-node-type` CLI filter — High-2
- Add a repeatable `--aggregate-node-type {botanical_family,functional_group,host_group}` to
  `plan-relationship-queries` and `discover-relationships`, threaded into
  `load_aggregate_nodes(node_types=...)`. Default unchanged (all three) for back-compat; the backbone
  runs pass `--aggregate-node-type functional_group` (and `host_group` for overlays).
- Test: planner scoped to `functional_group` yields 36 ordered / 21 unordered pairs.

### A7. Enforce `source_tier_id` — Med-1
- Add `source_tier_id` to `provenance.required` in
  [crop-relationship-claim.schema.json:181](../schemas/crop-relationship-claim.schema.json#L181)
  **and** add a guard in `validate_relationship_claims`
  ([relationship_pipeline.py:551](../src/crop_search_framework/relationship_pipeline.py#L551)) that
  marks a claim `rejected` (with reason) if the tier is missing or not in the manifest — so a typo'd
  tier can't silently rank as "unknown/worst".
- **Fixture audit:** all 22 live claims already carry `source_tier_id` (verified). Audit
  `tests/**` and `tests/golden/relationships/*` for claim-shaped fixtures lacking it and update them
  in the same change.

### A8. Merged multi-run graph + coverage report — High-3
The report must resolve across runs so a peer-reviewed upgrade in run C supersedes a backbone claim
in run B. Today `build_relationship_graph` / `resolve_crop_relationship` are single-run
([relationship_pipeline.py:744](../src/crop_search_framework/relationship_pipeline.py#L744),
[:820](../src/crop_search_framework/relationship_pipeline.py#L820)).
- Refactor: extract `_graph_from_claims(claims) -> graph` and `_resolve(graph, catalog, …)` so both
  single-run and merged paths share one implementation (no behavior change for existing callers).
- Add `build_merged_relationship_graph(repo_root, run_ids)`: load validated claims from every run,
  **dedupe by `relationship_claim_id`**, build one graph; persist to
  `exploration/relationships/graph/merged-<label>.json`.
- Add `relationship_coverage_report(repo_root, run_ids, modes)` →
  `exploration/relationships/coverage/coverage-<label>.json`: build the merged graph once, resolve
  all 55 pairs × mode against it, and report
  - **answerable** pairs / 55 per mode (direct + group-inferred),
  - **evidence-grade split** (`peer_reviewed` / `reference_backbone` / `none`) **× review status**
    (`accepted` vs `needs_review`) — see Med-3,
  - **upgrade candidates** (covered + grade `reference_backbone`),
  - `tier_superseded_conflict` / `ambiguous_effect` cells for review.

### A9. Review status is not certainty — Med-3
Matrix and graph currently treat `needs_review` as usable evidence alongside `accepted`
([:586](../src/crop_search_framework/relationship_pipeline.py#L586),
[:748](../src/crop_search_framework/relationship_pipeline.py#L748)). Keep that for *answerability*,
but the coverage report reports **`accepted`-backed coverage** as the headline number and
`needs_review` as a separate **provisional** column. Phase exit criteria (B3/C3) count
**accepted-backed** only.

### A10. CLI + extraction prompt
- CLI: `relationship-coverage-report --run-id … [--run-id …] [--mode rotation,intercrop]`.
- `prompts/relationships/extract-opus.md`: require `provenance.source_tier_id` on every claim;
  document that a peer-reviewed claim should **supersede** (not duplicate) the backbone claim for the
  same pair+mode+direction, and how `conditional` vs decisive effects are interpreted (A4).

### A11. Tests (extend existing suites)
- `connector_results_for_tier('reference_encyclopedia', …)` routes to Wikipedia; unknown → `([],[])`.
- `tier_rank_index` / `tier_band` incl. unknown-tier fallback.
- `tiered_effect`: peer-reviewed `incompatible` beats textbook `compatible` (→ `incompatible`,
  grade `peer_reviewed`, `tier_superseded_conflict`); top-tier positive+negative → `conflicting`;
  `conditional`+`beneficial` → `conditional` + `ambiguous_effect`; neutral-only → `neutral`.
- `--aggregate-node-type functional_group` → 36/21 pairs.
- Schema/validation rejects a claim missing `source_tier_id`.
- Merged graph: claim in run B + upgrading claim in run C → one cell, peer-reviewed wins.
- Coverage report: answerable count + grade×review-status split correct on a fixture.

---

## Part B — Build the functional-group backbone (data)

Goal: every crop pair answerable in both modes at `reference_backbone` grade.

### B1. Backbone group claims (scoped aggregate run)
- `plan-relationship-queries --node-mode aggregate --aggregate-node-type functional_group
  --pair-mode auto` for rotation and intercrop, scoped to backbone tiers (`--source-tier-id
  international_institution --source-tier-id extension_publication --source-tier-id
  textbook_reference`). Run modes separately so directional rotation and symmetric intercrop pair
  correctly. ~72 queries/tier (rotation) + ~42 (intercrop), not the 148+ of an unscoped run.
- `select-relationship-fetch` → `fetch-relationships` → `build-corpus`.
- **Manual Opus extraction:** ~36 rotation (directional) + ~21 intercrop (symmetric) group claims,
  each with honest `source_tier_id`. Optionally add the 2 `host_group` self-overlays.
- `build-relationship-graph` then `resolve` spot-checks: every one of the 55 pairs answers in both
  modes.

### B2. Direct backbone where groups mislead
Where the group answer is wrong for a specific pair (e.g. potato vs sugar_beet both `root_crop` but
rotate differently; allelopathy specifics), add a direct crop-pair backbone claim so the resolver
prefers it over the inference. Identify these from B1 spot-checks.

### B3. Backbone acceptance
`relationship-coverage-report` exit criteria: **accepted-backed** answerable coverage = 55/55 both
modes; intercrop no longer near-zero.

---

## Part C — Upgrade to peer-reviewed (data)

### C1. Prioritized upgrade queue
From the coverage report's **upgrade-candidate** list, order by importance (wheat hubs, cereal×legume
and cereal×oilseed rotations, corn–soybean intercrop first).

### C2. Peer-reviewed passes
- `discover-relationships --source-tier-id peer_reviewed_science` (crop `node_mode`) per queued pair
  → fetch → corpus → **manual extraction** of peer-reviewed claims (LER, yield Δ, DOI provenance).
- Re-run `relationship-coverage-report` across **all** run_ids (merged graph, A8). The peer-reviewed
  claim supersedes the backbone for that cell; grade moves `reference_backbone → peer_reviewed`.

### C3. Stopping rule (Decision D3)
Phase-1 target: the ~14 already-covered rotation pairs + top ~8 intercrop pairs reach `peer_reviewed`
(accepted). The long tail stays at `reference_backbone`, **reported as such** — no silent gaps.

---

## 4. Decisions for review (recommendations baked in)

- **D1 — Tier-conflict semantics.** *Recommended:* top tier sets the effect; lower-tier disagreement
  → `tier_superseded_conflict` (surfaced, never flips effect); `conditional` vs decisive →
  `ambiguous_effect` (A4). Full polarity table in A4.
- **D2 — Backbone band membership.** *Recommended:* backbone = {textbook_reference,
  international_institution, extension_publication, industry_grower_guide, reference_encyclopedia};
  evidence = {peer_reviewed_science}. Narrow to strictly textbook+FAO if preferred.
- **D3 — Phase-1 upgrade scope.** *Recommended:* upgrade the 14 rotation + top intercrop pairs;
  long tail stays at backbone grade, reported.
- **D4 — Build order.** *Recommended:* Part A first (coverage isn't honestly measurable without the
  merged tier-aware layer), then B then C.
- **D5 — Backbone node types.** *Recommended:* phase-1 backbone = `functional_group` (+ `host_group`
  overlays); add `botanical_family` only if the group answers prove too coarse (B2).

## 5. Out of scope
- Automated/unattended extraction (manual Opus gate stays; the program never invents facts).
- New relationship modes beyond rotation + intercrop.
- Tier-aware *re-fetching* changes (fetch is already tier-aware).

## 6. Risks
- **Backbone over-generalization** → mitigated by B2 direct overrides + `tier_superseded_conflict`.
- **Textbook fetchability** → backbone leans on FAO/extension/encyclopedia (D2), reported by tier.
- **Extraction effort** → ~57 backbone + N upgrade hand-extractions; the functional-group scoping
  (A6) is what keeps it bounded vs the 118-pair default.
- **needs_review inflation** → A9 reports accepted vs provisional separately.

## 7. Touchpoints (files)
- `config/source-tiers/default.json` (A1)
- `src/crop_search_framework/dev_tools/discovery_connectors.py` (A2)
- `src/crop_search_framework/source_tiers.py` (A3)
- `src/crop_search_framework/relationships.py` — `load_aggregate_nodes` plumbing for the node-type
  filter (A6)
- `src/crop_search_framework/relationship_pipeline.py` — `tiered_effect`,
  `populate_relationship_matrix`, `resolve_crop_relationship`, `validate_relationship_claims`,
  `build_merged_relationship_graph`, `_graph_from_claims`/`_resolve` refactor,
  `relationship_coverage_report` (A4–A9)
- `schemas/crop-relationship-claim.schema.json` (A7), `schemas/crop-relationship-matrix.schema.json` (A5)
- `src/crop_search_framework/cli.py` — `--aggregate-node-type`, `relationship-coverage-report` (A6, A10)
- `prompts/relationships/extract-opus.md` (A10)
- `tests/test_hybrid_relationship_graph.py`, `tests/test_relationship_pipeline.py`,
  `tests/test_relationship_fetch_robustness.py`, fixtures (A11)
- run artifacts under `exploration/relationships/{discovery,claims,matrix,graph,coverage}/` (B, C)
