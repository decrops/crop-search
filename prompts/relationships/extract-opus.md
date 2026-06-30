# Relationship Claim Extraction Prompt

Use this prompt in-session with Claude/Opus on relationship corpus documents.

Extract crop relationship claims only. Emit `crop-relationship-claim.schema.json`
objects. Use crop node fields for exact crop relationships and aggregate node
fields for family, functional-group, or host-group evidence. Do not emit a
parameter claim from the same evidence span.

Routing rule:

- `corn after soybean improves yield` is a relationship claim.
- `rotate wheat with a non-cereal` is a `management.rotation_recommendation`
  parameter claim, unless the source names an explicit crop-pair relationship.

## Crop vs aggregate claims (the level-of-evidence rule)

Emit the claim at the level the **source states it** — never generalize a
crop-specific finding into a group claim, and never narrow a group principle to a
single crop you happened to be searching for.

- **Crop-specific evidence → direct crop claim.** "Cotton after soybean yielded
  more, due to residual N from the soybean crop" names two crops, so set
  `subject_node_type=crop`/`subject_node_id=cotton` and
  `object_node_type=crop`/`object_node_id=soybean` (and the legacy
  `subject_crop_id`/`object_crop_id`). It is **not** a legume→cereal claim even
  though soybean is a legume.
- **Group-level evidence → aggregate claim.** "Cereals following a legume crop
  gain a nitrogen credit" states a functional-group principle, so set
  `subject_node_type=functional_group`/`subject_node_id=cereal` and
  `object_node_type=functional_group`/`object_node_id=legume`. No crop fields.
- **Family / host-group evidence** uses `botanical_family` or `host_group`
  nodes the same way (e.g. brassicaceae after brassicaceae; clubroot_host shared
  disease carryover).
- Aggregate node ids must match `config/relationships/node-catalog.json`
  (`cereal`, `legume`, `brassicaceae`, `clubroot_host`, …).

Aggregate-targeted documents still produce direct crop claims when the relevant
sentence names specific crops — extract both kinds from one document when both
levels of evidence are genuinely present. When in doubt, prefer the narrower
(direct) claim; do not invent group coverage.

## Intercropping and other symmetric modes

`intercrop`, `strip_crop`, `mixed_crop`, and `companion_crop` are **symmetric**:
subject and object are interchangeable. (Note `relay_crop` is directional — A
relayed into B is not the same as B relayed into A.)

- **Subtypes (intercrop):** `intercrop_compatibility`, `land_equivalent_ratio`,
  `row_arrangement`.
- **Pick one `effect`** (the schema's `effect` is a single string — never stack
  two):
  - `beneficial` — a *measured* advantage (land-equivalent ratio > 1, or a
    reported yield/▢ gain).
  - `compatible` — stated compatibility with no quantified advantage.
  - `neutral` — explicitly no net effect.
  - `incompatible` / `avoid` — measured disadvantage or strong competition.
  - `conditional` — depends on arrangement, density, or region.
  Choose the single best-supported label.
- **Capture intercrop context:** `context.arrangement` (e.g. alternating rows,
  strips), `context.row_ratio`, `context.plant_density_adjustment`,
  `context.temporal_offset`. Put a numeric land-equivalent ratio in `value`
  (`value_type: numeric`, or `range` with `range_min`/`range_max`).
- **Direction:** set `direction: simultaneous` (or `bidirectional`) for
  intercrop-family claims.
- Endpoint order does not matter for symmetric modes — the pipeline canonicalizes
  it — but emitting endpoints in alphabetical crop order keeps artifacts tidy.
- **Routing:** an explicit crop-pair statement ("maize–bean intercropping raised
  the land-equivalent ratio to 1.3") is a relationship claim; generic "consider
  intercropping" advice with no named partner stays parameter/management text.

## Source tier and the textbook → peer-reviewed upgrade

- **Every claim must set `provenance.source_tier_id`** — it is schema-required and
  must be one of the manifest tiers (`peer_reviewed_science`, `textbook_reference`,
  `international_institution`, `extension_publication`, `industry_grower_guide`,
  `reference_encyclopedia`). A missing or unknown tier drops the claim in
  validation. Tag it honestly from where the text actually came.
- **Backbone vs upgrade.** A `peer_reviewed_science` claim **supersedes** — does
  not duplicate — a backbone (textbook/institution/extension/encyclopedia) claim
  for the same pair + mode + direction. The best tier present decides the cell's
  effect; lower tiers only corroborate or flag a disagreement. So when you find a
  paper for a pair the backbone already covers, add the peer-reviewed claim (with
  its quantitative value/DOI) rather than editing the backbone one.
- **`conditional` is not a tie-breaker dodge.** If decisive evidence
  (`beneficial`/`compatible` vs `incompatible`/`avoid`) and a `conditional` claim
  coexist at the same tier, the resolver reports `conditional` and flags the
  ambiguity — so only use `conditional` when the source genuinely says "it
  depends," not to hedge a clear result.

Never mark a claim accepted because of model confidence. Use `needs_review`
unless a human review pass explicitly accepts it.
