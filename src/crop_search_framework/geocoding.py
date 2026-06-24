from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


CENSUS_GAZETTEER_YEAR = "2025"


def enrich_scope(scope: Dict[str, str]) -> Dict[str, Any]:
    enriched: Dict[str, Any] = dict(scope)
    record = gazetteer_record(scope)
    if not record:
        enriched.update(
            {
                "geo_id": "global" if scope.get("level") == "global" else "",
                "geocode_source": "not_applicable" if scope.get("level") == "global" else "missing_gazetteer_record",
                "geocode_confidence": "none",
                "coordinate_system": "EPSG:4326",
            }
        )
        return enriched
    enriched.update(record)
    return enriched


def gazetteer_record(scope: Dict[str, str]) -> Optional[Dict[str, Any]]:
    key = (scope.get("level", ""), scope.get("name", ""))
    census_record = census_record_for_scope(scope)
    if census_record:
        return census_record
    if key in CUSTOM_GAZETTEER:
        return dict(CUSTOM_GAZETTEER[key])
    normalized_key = (scope.get("level", ""), scope.get("name", "").lower())
    for (level, name), record in CUSTOM_GAZETTEER.items():
        if normalized_key == (level, name.lower()):
            return dict(record)
    return None


def census_record_for_scope(scope: Dict[str, str]) -> Optional[Dict[str, Any]]:
    level = scope.get("level", "")
    name = scope.get("name", "")
    if level == "state":
        return census_state_record(name)
    if level == "county":
        return census_county_record(name)
    return None


@lru_cache(maxsize=1)
def census_state_records() -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    for row in census_rows("state"):
        name = row["NAME"]
        record = census_point(row, "state")
        records[name.lower()] = record
        records[row["USPS"].lower()] = record
    return records


@lru_cache(maxsize=1)
def census_state_names_by_alias() -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for row in census_rows("state"):
        aliases[row["NAME"].lower()] = row["NAME"]
        aliases[row["USPS"].lower()] = row["NAME"]
    return aliases


@lru_cache(maxsize=1)
def census_state_names_by_abbr() -> Dict[str, str]:
    return {row["USPS"].upper(): row["NAME"] for row in census_rows("state")}


@lru_cache(maxsize=1)
def census_county_records_by_full_name() -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    state_names = census_state_names_by_abbr()
    for row in census_rows("counties"):
        state_name = state_names.get(row["USPS"], "")
        if not state_name:
            continue
        full_name = county_display_name(row["NAME"], state_name)
        record = census_point(row, "county")
        records[full_name.lower()] = record
        records["{0}, {1}".format(row["NAME"], row["USPS"]).lower()] = record
    return records


@lru_cache(maxsize=1)
def census_county_records_by_name_and_state() -> Dict[Tuple[str, str], Tuple[str, Dict[str, Any]]]:
    records: Dict[Tuple[str, str], Tuple[str, Dict[str, Any]]] = {}
    state_names = census_state_names_by_abbr()
    for row in census_rows("counties"):
        state_name = state_names.get(row["USPS"], "")
        if not state_name:
            continue
        full_name = county_display_name(row["NAME"], state_name)
        records[(row["NAME"].lower(), state_name.lower())] = (full_name, census_point(row, "county"))
    return records


@lru_cache(maxsize=1)
def census_county_records_by_name() -> Dict[str, List[Tuple[str, Dict[str, Any]]]]:
    records: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    state_names = census_state_names_by_abbr()
    for row in census_rows("counties"):
        state_name = state_names.get(row["USPS"], "")
        if not state_name:
            continue
        full_name = county_display_name(row["NAME"], state_name)
        records.setdefault(row["NAME"].lower(), []).append((full_name, census_point(row, "county")))
    return records


def census_rows(kind: str) -> List[Dict[str, str]]:
    path = gazetteer_data_path(kind)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="|"))


def gazetteer_data_path(kind: str) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    filename = "{0}_Gaz_{1}_national.txt".format(CENSUS_GAZETTEER_YEAR, kind)
    package_relative = repo_root / "data" / "gazetteer" / filename
    if package_relative.exists():
        return package_relative
    return Path.cwd() / "data" / "gazetteer" / filename


def census_point(row: Dict[str, str], level: str) -> Dict[str, Any]:
    return {
        "geo_id": "census:{0}".format(row["GEOIDFQ"]),
        "centroid": {
            "lat": float(row["INTPTLAT"]),
            "lon": float(row["INTPTLONG"]),
        },
        "geocode_source": "us_census_gazetteer_{0}:{1}".format(CENSUS_GAZETTEER_YEAR, level),
        "geocode_confidence": "approximate",
        "coordinate_system": "EPSG:4326",
    }


def census_state_record(name_or_abbr: str) -> Optional[Dict[str, Any]]:
    if not name_or_abbr:
        return None
    record = census_state_records().get(name_or_abbr.lower())
    return dict(record) if record else None


def census_county_record(name: str) -> Optional[Dict[str, Any]]:
    if not name:
        return None
    lowered = name.lower()
    full_name_record = census_county_records_by_full_name().get(lowered)
    if full_name_record:
        return dict(full_name_record)
    return None


def county_display_name(county_name: str, state_name: str) -> str:
    return "{0}, {1}".format(county_name, state_name)


def state_name_from_alias(value: str) -> str:
    normalized = value.strip().strip(",.").lower()
    return census_state_names_by_alias().get(normalized, "")


def state_scope_from_text(value: str) -> str:
    lowered = value.lower()
    for alias, state_name in sorted(census_state_names_by_alias().items(), key=lambda item: len(item[0]), reverse=True):
        if len(alias) == 2 and alias.isalpha():
            if re.search(r"\b{0}\b".format(re.escape(alias.upper())), value):
                return state_name
            continue
        if re.search(r"(?<![a-z]){0}(?![a-z])".format(re.escape(alias)), lowered):
            return state_name
    return ""


def county_scope_from_text(value: str, default_state_name: str = "") -> Optional[Dict[str, str]]:
    for county_name, state_hint in county_mentions(value):
        state_name = state_name_from_alias(state_hint) if state_hint else ""
        if not state_name:
            state_name = state_scope_from_text(value)
        if not state_name and default_state_name:
            state_name = default_state_name
        if state_name:
            match = census_county_records_by_name_and_state().get((county_name.lower(), state_name.lower()))
            if match:
                full_name, _ = match
                return {
                    "level": "county",
                    "name": full_name,
                }
            continue
        unique_matches = census_county_records_by_name().get(county_name.lower(), [])
        if len(unique_matches) == 1:
            full_name, _ = unique_matches[0]
            return {
                "level": "county",
                "name": full_name,
            }
    return None


def county_mentions(value: str) -> List[Tuple[str, str]]:
    mentions: List[Tuple[str, str]] = []
    pattern = re.compile(
        r"\b([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3})\s+"
        r"(County|Parish|Borough|Census Area|Municipality)"
        r"(?:,\s*([A-Z]{2}|[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?))?",
    )
    for match in pattern.finditer(value):
        county_name = "{0} {1}".format(match.group(1).strip(), match.group(2).strip())
        mentions.append((county_name, (match.group(3) or "").strip()))
    return mentions


def production_region_scope_from_text(value: str) -> Optional[Dict[str, str]]:
    lowered = value.lower()
    for region_name, aliases in sorted(PRODUCTION_REGION_ALIASES.items(), key=lambda item: max(len(alias) for alias in item[1]), reverse=True):
        for alias in aliases:
            if re.search(r"(?<![a-z]){0}(?![a-z])".format(re.escape(alias.lower())), lowered):
                return {
                    "level": "region",
                    "name": region_name,
                }
    return None


def point(
    geo_id: str,
    lat: float,
    lon: float,
    source: str,
    confidence: str,
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "geo_id": geo_id,
        "centroid": {
            "lat": lat,
            "lon": lon,
        },
        "geocode_source": source,
        "geocode_confidence": confidence,
        "coordinate_system": "EPSG:4326",
    }
    if bbox:
        record["bbox"] = bbox_record(*bbox)
    return record


def bbox_record(west: float, south: float, east: float, north: float) -> Dict[str, float]:
    return {
        "west": west,
        "south": south,
        "east": east,
        "north": north,
    }


CUSTOM_GAZETTEER: Dict[Tuple[str, str], Dict[str, Any]] = {
    ("country", "United States"): point(
        "iso3166-1:US",
        39.8283,
        -98.5795,
        "natural_earth:united_states_conterminous_approx",
        "approximate",
        (-124.7844, 24.7433, -66.9514, 49.3458),
    ),
    ("region", "Corn Belt"): point(
        "custom:region:corn_belt",
        41.5000,
        -91.5000,
        "custom_production_region_gazetteer:corn_belt",
        "approximate",
        (-97.5000, 37.0000, -82.0000, 45.5000),
    ),
    ("region", "southern Corn Belt"): point(
        "custom:region:southern_corn_belt",
        38.5000,
        -90.0000,
        "custom_production_region_gazetteer:southern_corn_belt",
        "approximate",
        (-97.5000, 35.0000, -82.0000, 41.0000),
    ),
    ("region", "High Plains"): point(
        "custom:region:high_plains",
        39.0000,
        -101.5000,
        "custom_production_region_gazetteer:high_plains",
        "approximate",
        (-105.0000, 32.0000, -96.0000, 49.0000),
    ),
    ("region", "Northern Great Plains"): point(
        "custom:region:northern_great_plains",
        47.0000,
        -103.0000,
        "custom_production_region_gazetteer:northern_great_plains",
        "approximate",
        (-109.0000, 43.0000, -96.0000, 49.0000),
    ),
    ("region", "Central Great Plains"): point(
        "custom:region:central_great_plains",
        39.0000,
        -100.0000,
        "custom_production_region_gazetteer:central_great_plains",
        "approximate",
        (-105.0000, 36.0000, -95.0000, 42.0000),
    ),
    ("region", "Southern Great Plains"): point(
        "custom:region:southern_great_plains",
        34.5000,
        -99.5000,
        "custom_production_region_gazetteer:southern_great_plains",
        "approximate",
        (-104.0000, 30.0000, -94.0000, 37.0000),
    ),
    ("region", "Northern Plains"): point(
        "custom:region:northern_plains",
        46.5000,
        -101.0000,
        "custom_production_region_gazetteer:northern_plains",
        "approximate",
        (-106.5000, 42.5000, -95.0000, 49.0000),
    ),
    ("region", "Mississippi Delta"): point(
        "custom:region:mississippi_delta",
        34.5000,
        -90.5000,
        "custom_production_region_gazetteer:mississippi_delta",
        "approximate",
        (-92.5000, 30.5000, -88.0000, 37.0000),
    ),
    ("region", "Arkansas Grand Prairie"): point(
        "custom:region:arkansas_grand_prairie",
        34.7000,
        -91.5000,
        "custom_production_region_gazetteer:arkansas_grand_prairie",
        "approximate",
        (-92.3000, 34.1000, -90.8000, 35.3000),
    ),
    ("region", "Texas High Plains"): point(
        "custom:region:texas_high_plains",
        34.5000,
        -101.9000,
        "custom_production_region_gazetteer:texas_high_plains",
        "approximate",
        (-103.1000, 31.5000, -100.0000, 36.5000),
    ),
    ("region", "Pacific Northwest"): point(
        "custom:region:pacific_northwest",
        45.8000,
        -120.5000,
        "custom_production_region_gazetteer:pacific_northwest",
        "approximate",
        (-124.8000, 41.9000, -111.0000, 49.0000),
    ),
    ("region", "Columbia Basin"): point(
        "custom:region:columbia_basin",
        46.6000,
        -119.2000,
        "custom_production_region_gazetteer:columbia_basin",
        "approximate",
        (-121.5000, 44.5000, -116.0000, 48.5000),
    ),
    ("region", "Central Valley"): point(
        "custom:region:central_valley",
        37.3000,
        -120.5000,
        "custom_production_region_gazetteer:central_valley",
        "approximate",
        (-122.4000, 35.0000, -119.0000, 40.5000),
    ),
    ("region", "Southeast"): point(
        "custom:region:southeast_us",
        32.5000,
        -84.0000,
        "custom_production_region_gazetteer:southeast_us",
        "approximate",
        (-91.5000, 25.0000, -75.0000, 37.5000),
    ),
    ("region", "Mid-Atlantic"): point(
        "custom:region:mid_atlantic_us",
        39.0000,
        -76.5000,
        "custom_production_region_gazetteer:mid_atlantic_us",
        "approximate",
        (-80.6000, 35.5000, -73.5000, 42.5000),
    ),
    ("farm", "ISU Northeast Research Farm, Nashua, Iowa"): point(
        "osm:way:15896307",
        42.9362800,
        -92.5688524,
        "nominatim:3321_290th_street_nashua_ia",
        "exact",
        (-92.5689024, 42.9362300, -92.5688024, 42.9363300),
    ),
}


PRODUCTION_REGION_ALIASES: Dict[str, Tuple[str, ...]] = {
    "Arkansas Grand Prairie": ("arkansas grand prairie", "grand prairie region"),
    "Central Great Plains": ("central great plains",),
    "Central Valley": ("central valley", "san joaquin valley", "sacramento valley"),
    "Columbia Basin": ("columbia basin",),
    "Corn Belt": ("corn belt",),
    "High Plains": ("high plains",),
    "Mid-Atlantic": ("mid-atlantic", "mid atlantic"),
    "Mississippi Delta": ("mississippi delta", "delta region", "midsouth", "mid-south"),
    "Northern Great Plains": ("northern great plains",),
    "Northern Plains": ("northern plains",),
    "Pacific Northwest": ("pacific northwest", "pnw"),
    "Southeast": ("southeast", "southeastern united states", "southeastern u.s."),
    "southern Corn Belt": ("southern corn belt",),
    "Southern Great Plains": ("southern great plains",),
    "Texas High Plains": ("texas high plains",),
}
