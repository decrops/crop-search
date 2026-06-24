"""Render normalized claims into tagged, interlinked Obsidian markdown notes.

Deterministic rendering only — the LLM does extraction (judgment), this code
turns the resulting structured claims into consistent vault notes. Output:

* **data-point notes** — one per (crop x parameter), with faceted frontmatter
  ``tags:`` plus ``[[wikilinks]]`` to entity hubs,
* **entity hub notes** — crop / parameter / domain / source (and method /
  organism when the claims carry them),
* **MOC index notes**.

Vault safety: every generated file carries ``generated_by: crop-search`` in
frontmatter; the renderer only ever overwrites files bearing that marker, only
ever writes under the configured subdir, and supports ``dry_run`` / ``prune``.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

GENERATED_MARKER = "crop-search"

TIER_TAG = {
    "peer_reviewed_science": "peer-reviewed",
    "textbook_reference": "textbook",
    "international_institution": "institution",
    "extension_publication": "extension",
    "industry_grower_guide": "grower",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def slugify(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (value or "").lower())).strip("-")


def title_case(value: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[\s_]+", value or "") if w)


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", value or "").strip()
    return re.sub(r"\s+", " ", cleaned)


def load_manifest_index(repo_root: Path) -> Dict[str, Dict[str, Any]]:
    path = repo_root / "config" / "parameters" / "core-crop-parameters.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    index = {}
    for p in manifest.get("parameters", []):
        index[p["parameter_id"]] = {
            "label": p.get("label", p["parameter_id"]),
            "domain": p.get("domain") or p.get("family", ""),
            "family": p.get("family", ""),
            "parameter_kind": p.get("parameter_kind", "trait"),
        }
    return index, manifest.get("manifest_version", "")


def load_claims(repo_root: Path, run_id: str, subdir: str) -> List[Dict[str, Any]]:
    claim_dir = repo_root / "exploration" / subdir / run_id
    claims = []
    for path in sorted(claim_dir.glob("*claim*.json")):
        try:
            claims.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return claims


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #
def _value_display(value: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], str]:
    vt = value.get("value_type")
    unit = value.get("unit") or value.get("normalized_unit") or ""
    if vt == "numeric":
        n = value.get("numeric_value", value.get("normalized_value"))
        return (n, n, unit)
    if vt == "range":
        return (value.get("range_min"), value.get("range_max"), unit)
    return (None, None, unit)


def summarize_values(claims: List[Dict[str, Any]]) -> Dict[str, Any]:
    lows, highs, units = [], [], []
    numeric_count = 0
    for c in claims:
        lo, hi, unit = _value_display(c.get("value", {}))
        if lo is not None and hi is not None:
            lows.append(lo)
            highs.append(hi)
            numeric_count += 1
            if unit:
                units.append(unit)
    unit = max(set(units), key=units.count) if units else ""
    if numeric_count:
        lo, hi = min(lows), max(highs)
        if lo == hi:
            summary = "{0}{1}".format(_fmt(lo), _unit_suffix(unit))
        else:
            summary = "{0}–{1}{2}".format(_fmt(lo), _fmt(hi), _unit_suffix(unit))
    else:
        summary = "qualitative ({0} note{1})".format(len(claims), "" if len(claims) == 1 else "s")
    return {"summary": summary, "unit": unit, "numeric_count": numeric_count}


def _fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else str(n)


def _unit_suffix(unit: str) -> str:
    if not unit:
        return ""
    if unit == "celsius":
        return " °C"
    return " {0}".format(unit)


def best_confidence(claims: List[Dict[str, Any]]) -> str:
    order = {"high": 3, "medium": 2, "low": 1}
    best = max((order.get(c.get("confidence", "low"), 1) for c in claims), default=1)
    return {3: "high", 2: "medium", 1: "low"}[best]


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _frontmatter(fields: Dict[str, Any], tags: List[str]) -> str:
    lines = ["---"]
    for k, v in fields.items():
        if isinstance(v, list):
            lines.append("{0}: [{1}]".format(k, ", ".join(str(x) for x in v)))
        else:
            lines.append('{0}: "{1}"'.format(k, v) if isinstance(v, str) and (":" in v or "—" in v) else "{0}: {1}".format(k, v))
    lines.append("tags:")
    for t in tags:
        lines.append("  - {0}".format(t))
    lines.append("---")
    return "\n".join(lines)


def render_data_point_note(
    crop: str, parameter_id: str, claims: List[Dict[str, Any]],
    meta: Dict[str, Any], manifest_version: str, run_id: str, generated_at: str,
) -> Tuple[str, str]:
    label = meta["label"]
    domain = meta["domain"]
    kind = meta["parameter_kind"]
    param_slug = slugify(parameter_id.split(".")[-1])
    crop_title = title_case(crop)

    vals = summarize_values(claims)
    conf = best_confidence(claims)
    tiers = sorted({(c.get("provenance") or {}).get("source_tier_id", "") for c in claims} - {""})
    methods = sorted({m for c in claims for m in (c.get("methods") or [])})
    organisms = sorted({o.get("name") if isinstance(o, dict) else o
                        for c in claims for o in (c.get("organisms") or [])} - {None, ""})
    bbch = sorted({str(c.get("bbch_applicability", {}).get("bbch_min"))
                   for c in claims if c.get("bbch_applicability")} - {"None"})

    tags = [
        "crop/{0}".format(slugify(crop)),
        "domain/{0}".format(slugify(domain)),
        "param/{0}".format(param_slug),
        "kind/{0}".format(slugify(kind)),
        "confidence/{0}".format(conf),
    ]
    tags += ["source-tier/{0}".format(TIER_TAG.get(t, slugify(t))) for t in tiers]
    tags += ["method/{0}".format(slugify(m)) for m in methods]
    tags += ["pest/{0}".format(slugify(o)) for o in organisms]
    tags += ["stage/bbch-{0}".format(b) for b in bbch]

    fields = {
        "title": "{0} — {1}".format(crop_title, label),
        "type": "crop-parameter",
        "crop": slugify(crop),
        "parameter": parameter_id,
        "parameter_label": label,
        "domain": slugify(domain),
        "parameter_kind": kind,
        "value_summary": vals["summary"],
        "unit": vals["unit"],
        "confidence": conf,
        "source_count": len({(c.get("provenance") or {}).get("source_urls", [""])[0] for c in claims}),
        "run_id": run_id,
        "manifest_version": manifest_version,
        "generated_by": GENERATED_MARKER,
        "generated_at": generated_at,
    }

    links = ["[[{0}]]".format(crop_title), "[[{0}]]".format(label), "[[{0} (domain)]]".format(title_case(domain))]
    links += ["[[{0}]]".format(title_case(m)) for m in methods]
    links += ["[[{0}]]".format(title_case(o)) for o in organisms]

    body = [
        _frontmatter(fields, tags),
        "",
        "# {0} — {1}".format(crop_title, label),
        "",
        "**Value:** {0}".format(vals["summary"]),
        "**Confidence:** {0}  ·  **Sources:** {1}".format(conf, fields["source_count"]),
        "**Links:** " + " · ".join(links),
        "",
        "## Sourced values",
        "",
        "| Value | Qualifier | Source | Tier | Conf |",
        "|---|---|---|---|---|",
    ]
    for c in _dedupe_rows(claims):
        lo, hi, unit = _value_display(c.get("value", {}))
        if lo is not None:
            val = "{0}{1}".format(_fmt(lo), _unit_suffix(unit)) if lo == hi else "{0}–{1}{2}".format(_fmt(lo), _fmt(hi), _unit_suffix(unit))
        else:
            val = (c.get("value", {}).get("text_value") or "")[:60] or "—"
        prov = c.get("provenance") or {}
        src_title = prov.get("source_title") or prov.get("source_domain") or "source"
        body.append("| {0} | {1} | [[{2}]] | {3} | {4} |".format(
            val,
            c.get("value", {}).get("qualifier", ""),
            safe_filename(src_title)[:70],
            TIER_TAG.get(prov.get("source_tier_id", ""), prov.get("source_tier_id", "")),
            c.get("confidence", ""),
        ))

    body += ["", "## Evidence", ""]
    for c in _dedupe_rows(claims)[:5]:
        prov = c.get("provenance") or {}
        quote = (c.get("claim_text") or "").strip().replace("\n", " ")
        if quote:
            body.append("> {0} — [[{1}]]".format(quote[:300], safe_filename(prov.get("source_title") or "source")[:70]))
    body += ["", "## Related", "", "[[{0}]] · [[{1} (domain)]]".format(crop_title, title_case(domain)), ""]

    filename = "{0} — {1}.md".format(crop_title, safe_filename(label))
    return filename, "\n".join(body)


def _dedupe_rows(claims: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for c in claims:
        prov = c.get("provenance") or {}
        key = (tuple(prov.get("source_urls", [])), json.dumps(c.get("value", {}).get("raw_value_text", ""))[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def render_entity_note(name: str, etype: str, generated_at: str, extra_lines: Optional[List[str]] = None) -> Tuple[str, str]:
    fields = {"title": name, "type": etype, "generated_by": GENERATED_MARKER, "generated_at": generated_at}
    tags = ["entity/{0}".format(slugify(etype))]
    body = [_frontmatter(fields, tags), "", "# {0}".format(name), "", "*{0} hub.*".format(title_case(etype))]
    if extra_lines:
        body += [""] + extra_lines
    return "{0}.md".format(safe_filename(name)), "\n".join(body) + "\n"


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def render_vault(
    repo_root: Path, run_id: str, vault_path: Path, subdir: str,
    claims_subdir: str = "normalized", dry_run: bool = False, prune: bool = False,
    generated_at: str = "1970-01-01",
) -> Dict[str, Any]:
    manifest_index, manifest_version = load_manifest_index(repo_root)
    claims = load_claims(repo_root, run_id, claims_subdir)

    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for c in claims:
        crop = (c.get("entity") or {}).get("name", "")
        pid = c.get("parameter_id", "")
        if crop and pid in manifest_index:
            groups[(crop, pid)].append(c)

    target_dir = vault_path / subdir
    planned: List[Tuple[Path, str]] = []
    entities: Dict[str, str] = {}   # name -> type
    sources: Dict[str, Dict[str, Any]] = {}

    # Backlink accumulators so every hub note has a useful body (lists the
    # data-point notes that reference it), not just an empty stub.
    crop_index: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))  # crop -> domain -> [rows]
    param_index: Dict[str, List[str]] = defaultdict(list)   # parameter label -> [rows]
    domain_index: Dict[str, List[str]] = defaultdict(list)  # "X (domain)" -> [rows]
    source_index: Dict[str, List[str]] = defaultdict(list)  # source -> [data-point links]

    crops_seen, domains_seen = set(), set()
    for (crop, pid), group in sorted(groups.items()):
        meta = manifest_index[pid]
        filename, content = render_data_point_note(crop, pid, group, meta, manifest_version, run_id, generated_at)
        planned.append((target_dir / filename, content))

        crop_title = title_case(crop)
        domain_title = title_case(meta["domain"])
        label = meta["label"]
        note_link = "[[{0} — {1}]]".format(crop_title, label)
        vals = summarize_values(group)
        n_sources = len({(c.get("provenance") or {}).get("source_urls", [""])[0] for c in group})
        row = "- {0} — **{1}** ({2} sources)".format(note_link, vals["summary"], n_sources)

        crops_seen.add(crop_title)
        domains_seen.add(domain_title)
        entities[crop_title] = "crop"
        entities["{0} (domain)".format(domain_title)] = "domain"
        entities[label] = "parameter"
        crop_index[crop_title][domain_title].append(row)
        param_index[label].append("- {0} — **{1}** ({2} sources)".format(note_link, vals["summary"], n_sources))
        domain_index["{0} (domain)".format(domain_title)].append(row)
        for c in group:
            prov = c.get("provenance") or {}
            st = prov.get("source_title")
            if st:
                sources[safe_filename(st)] = prov
                if note_link not in source_index[safe_filename(st)]:
                    source_index[safe_filename(st)].append(note_link)

    # entity hub notes — now with informative bodies
    for name, etype in entities.items():
        if etype == "crop":
            extra = []
            for domain in sorted(crop_index[name]):
                extra += ["## {0}".format(domain), ""] + sorted(crop_index[name][domain]) + [""]
        elif etype == "parameter":
            extra = ["## Data points", ""] + sorted(param_index[name])
        elif etype == "domain":
            extra = ["## Parameters", ""] + sorted(domain_index[name])
        else:
            extra = []
        fn, content = render_entity_note(name, etype, generated_at, extra)
        planned.append((target_dir / fn, content))
    # source notes — metadata plus what they back
    for st, prov in sources.items():
        extra = [
            "- URL: {0}".format((prov.get("source_urls") or [""])[0]),
            "- Domain: {0}".format(prov.get("source_domain", "")),
            "- Tier: {0}".format(prov.get("source_tier_label", "")),
            "- DOI: {0}".format((prov.get("source_metadata") or {}).get("doi", "") if isinstance(prov.get("source_metadata"), dict) else ""),
            "",
            "## Cited by",
            "",
        ] + sorted(source_index[st])
        fn, content = render_entity_note(st, "source", generated_at, extra)
        planned.append((target_dir / fn, content))

    # MOC index
    moc_lines = ["## Crops", ""] + ["- [[{0}]]".format(c) for c in sorted(crops_seen)]
    moc_lines += ["", "## Domains", ""] + ["- [[{0} (domain)]]".format(d) for d in sorted(domains_seen)]
    fn, content = render_entity_note("DeCrops Research — Crop Science (Index)", "moc", generated_at, moc_lines)
    planned.append((target_dir / fn, content))

    written, skipped = _commit(planned, dry_run)
    return {
        "run_id": run_id,
        "data_point_notes": len(groups),
        "entity_notes": len(entities),
        "source_notes": len(sources),
        "files_planned": len(planned),
        "files_written": written,
        "files_skipped_foreign": skipped,
        "dry_run": dry_run,
        "target_dir": str(target_dir),
    }


def _is_ours(path: Path) -> bool:
    if not path.exists():
        return True
    head = path.read_text(encoding="utf-8", errors="ignore")[:600]
    return "generated_by: {0}".format(GENERATED_MARKER) in head


def _commit(planned: List[Tuple[Path, str]], dry_run: bool) -> Tuple[int, int]:
    written = skipped = 0
    for path, content in planned:
        if not _is_ours(path):
            skipped += 1
            continue
        if dry_run:
            written += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
        written += 1
    return written, skipped
