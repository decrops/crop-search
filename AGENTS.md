# Agent Session Instructions

This repo uses `docs/HANDOFF.md` as the canonical continuity file and `docs/CAPABILITY_MAP.md` as the living can/cannot-do inventory.

At the start of every future coding session:

- Read `docs/HANDOFF.md` first.
- Read `docs/CAPABILITY_MAP.md` second to understand current pipeline capabilities, constraints, and missing pieces.
- Read `README.md` for command overview if needed.
- Read `docs/IMPLEMENTATION_LOG.md` only when historical detail is needed.

Before ending any session that changes code, configs, generated artifacts, or docs:

- Update `docs/IMPLEMENTATION_LOG.md` for meaningful milestones.
- Run `PYTHONPATH=src python3 -m crop_search_framework.cli write-capability-map`.
- Run `PYTHONPATH=src python3 -m crop_search_framework.cli write-handoff`.
- Run relevant validation, normally `PYTHONPATH=src python3 -m unittest discover -s tests` and `PYTHONPATH=src python3 -m compileall src tests`.

Also refresh `docs/CAPABILITY_MAP.md` whenever a capability changes status, a new pipeline stage is added, a known limitation is removed, or a new constraint is discovered.

Default next-search posture:

- Prefer `config/runs/pilot-global-*.json` for new exploration runs unless the user explicitly asks for a U.S.-scoped pilot.
- Preserve source-tier coverage across peer-reviewed science, open textbook/reference material, international institutions, extension/public agronomy publications, and industry/grower guides.
- Do not extract claims from paywalled scientific papers or books unless accessible text is available; metadata-only discovery is acceptable.
