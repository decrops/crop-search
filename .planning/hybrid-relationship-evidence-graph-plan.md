# Hybrid Relationship Evidence Graph Plan

## Scope

Implement a non-breaking hybrid relationship lane that keeps the current dense
crop-pair matrix for major agricultural and horticultural crops, adds a coarser
relationship evidence graph for minor crops and aggregate nodes, and resolves
minor-crop questions at request time from exact, genus, family, functional-group,
and host-risk evidence.

Extraction remains an in-session Claude/Opus workflow. This plan intentionally
does not add Ollama, a local LLM daemon, or OpenAI API extraction. No extracted
claim is auto-accepted from model confidence alone.

## Pipeline Shape

1. Keep the existing crop-level relationship matrix and claim schema valid.
2. Add a separate relationship node catalog for crop, family, functional-group,
   and host-risk nodes. Crop profiles remain unchanged.
3. Add an unordered discovery planning mode for rotation-style searches. A crop
   universe of `n` crops emits `n(n+1)/2` search pairs, while matrix population
   still writes ordered cells after extraction.
4. Build a relationship evidence graph from validated relationship claims:
   exact crop claims, aggregate group claims, host-risk overlays, provenance,
   and status.
5. Resolve minor crop pairs by checking exact crop evidence first, then group
   evidence, while always evaluating host-risk caveats.
6. Prevent cross-lane duplication: the same evidence span must not be emitted as
   both a relationship claim and a `management.rotation_recommendation`
   parameter claim.

## CLI Additions

- `plan-relationship-queries --pair-mode unordered`
- `discover-relationships --pair-mode unordered`
- `build-relationship-graph <run_id>`
- `resolve-crop-relationship <run_id> --subject <crop_or_alias> --object <crop_or_alias>`

## Cost And Time Guardrails

The 120-crop dense directed matrix remains computationally sensitive. The
unordered planner reduces discovery from `120 * 120 = 14,400` crop-pair searches
to `120 * 121 / 2 = 7,260` unordered crop-pair searches before source-tier and
query-template multiplication. With the current default five source tiers and
one query template per pair, that is roughly 36,300 discovery queries for
rotation alone.

Recommended posture:

- Keep 120 crops as the upper planning scenario, not the default live crawl.
- Run pilot slices first: 7 crops, then 25 crops, then sampled 120-crop batches.
- Use monthly subscriptions as the budget boundary for in-session extraction:
  Claude/Opus extraction is paced manually and not charged through an OpenAI API
  pipeline in this repo.
- Treat discovery/fetch compute on a VPS as mostly wall-clock, HTTP/cache, and
  storage time; the sensitive cost is model review/extraction time once sources
  have been selected.

## Remaining Gated Work

Live crawling, in-session Opus extraction, repair, and human acceptance remain
manual gates. The implementation here provides the contracts, planner, graph,
resolver, prompts, and tests needed before those gated runs.
