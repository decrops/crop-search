from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List


class PostgresLoader:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def export_sql(self, run_id: str) -> Path:
        normalized_dir = self.repo_root / "exploration" / "normalized" / run_id
        output_dir = self.repo_root / "data" / "postgres"
        output_dir.mkdir(parents=True, exist_ok=True)
        sql_path = output_dir / "load-{0}.sql".format(run_id)

        claim_files = sorted(path for path in normalized_dir.glob("*.json") if path.name != "summary.json")
        statements: List[str] = []
        for claim_file in claim_files:
            claim = self._load_json(claim_file)
            statements.extend(self._sql_for_claim(claim))
        durable_path = self.repo_root / "memory" / "durable" / run_id / "claims.json"
        if durable_path.exists():
            durable_report = self._load_json(durable_path)
            for durable_claim in durable_report.get("promoted_claims", []):
                statements.append(self._sql_for_durable_claim(durable_claim))
        sql_path.write_text("\n".join(statements) + ("\n" if statements else ""), encoding="utf-8")
        return sql_path

    def load_if_configured(self, run_id: str) -> Dict[str, Any]:
        sql_path = self.export_sql(run_id)
        dsn = os.environ.get("POSTGRES_DSN") or os.environ.get("DATABASE_URL")
        if not dsn:
            return {
                "mode": "sql-export",
                "sql_path": str(sql_path.relative_to(self.repo_root)),
                "loaded": False,
            }

        try:
            import psycopg
        except Exception as exc:
            return {
                "mode": "sql-export",
                "sql_path": str(sql_path.relative_to(self.repo_root)),
                "loaded": False,
                "reason": "psycopg not installed: {0}".format(exc),
            }

        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(self._schema_sql())
                cursor.execute(sql_path.read_text(encoding="utf-8"))
                cursor.execute(
                    "SELECT COUNT(*) FROM normalized_claims WHERE run_id = %s",
                    (run_id,),
                )
                claim_count = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT COUNT(*) FROM source_documents WHERE latest_run_id = %s",
                    (run_id,),
                )
                source_count = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT COUNT(*) FROM durable_claims WHERE run_id = %s",
                    (run_id,),
                )
                durable_claim_count = cursor.fetchone()[0]
            connection.commit()
        return {
            "mode": "database-load",
            "sql_path": str(sql_path.relative_to(self.repo_root)),
            "loaded": True,
            "source_count": source_count,
            "claim_count": claim_count,
            "durable_claim_count": durable_claim_count,
        }

    def _schema_sql(self) -> str:
        migrations_dir = self.repo_root / "data" / "postgres" / "migrations"
        migrations = sorted(migrations_dir.glob("*.sql")) if migrations_dir.exists() else []
        if migrations:
            return "\n\n".join(path.read_text(encoding="utf-8") for path in migrations)
        return (self.repo_root / "data" / "postgres" / "schema.sql").read_text(encoding="utf-8")

    def _sql_for_claim(self, claim: Dict[str, Any]) -> Iterable[str]:
        provenance = claim["provenance"]
        value = claim["value"]
        source_url = provenance["source_urls"][0]
        location_scope = claim["location_scope"]
        source_geo_scope = claim.get("source_geo_scope", {"level": "global", "name": "global"})
        geo_evidence = claim.get(
            "geo_evidence",
            {
                "claim_location_source": "default_global",
                "claim_location_confidence": "none",
                "claim_location_text": "",
                "source_location_source": "default_global",
                "source_location_confidence": "none",
                "source_location_text": "",
                "matched_locations": [],
            },
        )
        source_insert = """
INSERT INTO source_documents (
  source_url, source_title, source_domain, document_type, accessed_at,
  source_publication_date, source_publication_year, latest_run_id
)
VALUES (
  {source_url}, {source_title}, {source_domain}, {document_type}, {accessed_at},
  {source_publication_date}, {source_publication_year}, {run_id}
)
ON CONFLICT (source_url) DO UPDATE SET
  source_title = EXCLUDED.source_title,
  source_domain = EXCLUDED.source_domain,
  document_type = EXCLUDED.document_type,
  accessed_at = EXCLUDED.accessed_at,
  source_publication_date = EXCLUDED.source_publication_date,
  source_publication_year = EXCLUDED.source_publication_year,
  latest_run_id = EXCLUDED.latest_run_id;
""".format(
            source_url=sql_quote(source_url),
            source_title=sql_quote(provenance["source_title"]),
            source_domain=sql_quote(provenance["source_domain"]),
            document_type=sql_quote(provenance["document_type"]),
            accessed_at=sql_quote(provenance["accessed_at"]),
            source_publication_date=sql_quote(provenance["source_publication_date"])
            if provenance.get("source_publication_date")
            else "NULL",
            source_publication_year=sql_nullable_number(provenance.get("source_publication_year")),
            run_id=sql_quote(claim["run_id"]),
        ).strip()

        claim_insert = """
INSERT INTO normalized_claims (
  claim_id, run_id, entity_type, entity_name, parameter_id, attribute, attribute_subtype, observation_type, claim_text,
  value_type, raw_value_text, numeric_value, text_value, range_min, range_max,
  normalized_numeric_value, normalized_range_min, normalized_range_max, unit, normalized_unit, qualifier,
  location_level, location_name, location_geo_id, location_centroid_lat, location_centroid_lon, location_bbox_json,
  source_location_level, source_location_name, source_geo_id, source_centroid_lat, source_centroid_lon, source_bbox_json,
  source_geo_scope_json, geo_evidence_json,
  time_label, source_url, source_title, source_domain,
  document_type, evidence_text, confidence, conflict_status, conflict_reason, status
) VALUES (
  {claim_id}, {run_id}, {entity_type}, {entity_name}, {parameter_id}, {attribute}, {attribute_subtype}, {observation_type}, {claim_text},
  {value_type}, {raw_value_text}, {numeric_value}, {text_value}, {range_min}, {range_max},
  {normalized_numeric_value}, {normalized_range_min}, {normalized_range_max}, {unit}, {normalized_unit}, {qualifier},
  {location_level}, {location_name}, {location_geo_id}, {location_centroid_lat}, {location_centroid_lon}, {location_bbox_json},
  {source_location_level}, {source_location_name}, {source_geo_id}, {source_centroid_lat}, {source_centroid_lon}, {source_bbox_json},
  {source_geo_scope_json}::jsonb, {geo_evidence_json}::jsonb,
  {time_label}, {source_url}, {source_title}, {source_domain},
  {document_type}, {evidence_text}, {confidence}, {conflict_status}, {conflict_reason}, {status}
)
ON CONFLICT (claim_id) DO UPDATE SET
  run_id = EXCLUDED.run_id,
  parameter_id = EXCLUDED.parameter_id,
  attribute = EXCLUDED.attribute,
  attribute_subtype = EXCLUDED.attribute_subtype,
  observation_type = EXCLUDED.observation_type,
  claim_text = EXCLUDED.claim_text,
  value_type = EXCLUDED.value_type,
  raw_value_text = EXCLUDED.raw_value_text,
  numeric_value = EXCLUDED.numeric_value,
  text_value = EXCLUDED.text_value,
  range_min = EXCLUDED.range_min,
  range_max = EXCLUDED.range_max,
  normalized_numeric_value = EXCLUDED.normalized_numeric_value,
  normalized_range_min = EXCLUDED.normalized_range_min,
  normalized_range_max = EXCLUDED.normalized_range_max,
  unit = EXCLUDED.unit,
  normalized_unit = EXCLUDED.normalized_unit,
  qualifier = EXCLUDED.qualifier,
  location_level = EXCLUDED.location_level,
  location_name = EXCLUDED.location_name,
  location_geo_id = EXCLUDED.location_geo_id,
  location_centroid_lat = EXCLUDED.location_centroid_lat,
  location_centroid_lon = EXCLUDED.location_centroid_lon,
  location_bbox_json = EXCLUDED.location_bbox_json,
  source_location_level = EXCLUDED.source_location_level,
  source_location_name = EXCLUDED.source_location_name,
  source_geo_id = EXCLUDED.source_geo_id,
  source_centroid_lat = EXCLUDED.source_centroid_lat,
  source_centroid_lon = EXCLUDED.source_centroid_lon,
  source_bbox_json = EXCLUDED.source_bbox_json,
  source_geo_scope_json = EXCLUDED.source_geo_scope_json,
  geo_evidence_json = EXCLUDED.geo_evidence_json,
  time_label = EXCLUDED.time_label,
  source_url = EXCLUDED.source_url,
  source_title = EXCLUDED.source_title,
  source_domain = EXCLUDED.source_domain,
  document_type = EXCLUDED.document_type,
  evidence_text = EXCLUDED.evidence_text,
  confidence = EXCLUDED.confidence,
  conflict_status = EXCLUDED.conflict_status,
  conflict_reason = EXCLUDED.conflict_reason,
  status = EXCLUDED.status;
""".format(
            claim_id=sql_quote(claim["claim_id"]),
            run_id=sql_quote(claim["run_id"]),
            entity_type=sql_quote(claim["entity"]["entity_type"]),
            entity_name=sql_quote(claim["entity"]["name"]),
            parameter_id=sql_quote(claim.get("parameter_id", "")),
            attribute=sql_quote(claim["attribute"]),
            attribute_subtype=sql_quote(claim.get("attribute_subtype", claim["attribute"])),
            observation_type=sql_quote(claim["observation_type"]),
            claim_text=sql_quote(claim["claim_text"]),
            value_type=sql_quote(value["value_type"]),
            raw_value_text=sql_quote(value["raw_value_text"]),
            numeric_value=sql_nullable_number(value.get("numeric_value")),
            text_value=sql_quote(value["text_value"]) if "text_value" in value else "NULL",
            range_min=sql_nullable_number(value.get("range_min")),
            range_max=sql_nullable_number(value.get("range_max")),
            normalized_numeric_value=sql_nullable_number(value.get("normalized_numeric_value")),
            normalized_range_min=sql_nullable_number(value.get("normalized_range_min")),
            normalized_range_max=sql_nullable_number(value.get("normalized_range_max")),
            unit=sql_quote(value["unit"]) if "unit" in value else "NULL",
            normalized_unit=sql_quote(value["normalized_unit"]) if "normalized_unit" in value else "NULL",
            qualifier=sql_quote(value["qualifier"]) if "qualifier" in value else "NULL",
            location_level=sql_quote(location_scope["level"]),
            location_name=sql_quote(location_scope["name"]),
            location_geo_id=sql_quote(location_scope["geo_id"]) if location_scope.get("geo_id") else "NULL",
            location_centroid_lat=sql_nullable_number(centroid_lat(location_scope)),
            location_centroid_lon=sql_nullable_number(centroid_lon(location_scope)),
            location_bbox_json=sql_json_or_null(location_scope.get("bbox")),
            source_location_level=sql_quote(source_geo_scope["level"]),
            source_location_name=sql_quote(source_geo_scope["name"]),
            source_geo_id=sql_quote(source_geo_scope["geo_id"]) if source_geo_scope.get("geo_id") else "NULL",
            source_centroid_lat=sql_nullable_number(centroid_lat(source_geo_scope)),
            source_centroid_lon=sql_nullable_number(centroid_lon(source_geo_scope)),
            source_bbox_json=sql_json_or_null(source_geo_scope.get("bbox")),
            source_geo_scope_json=sql_quote(json.dumps(source_geo_scope, sort_keys=True)),
            geo_evidence_json=sql_quote(json.dumps(geo_evidence, sort_keys=True)),
            time_label=sql_quote(claim["time_scope"]["label"]),
            source_url=sql_quote(source_url),
            source_title=sql_quote(provenance["source_title"]),
            source_domain=sql_quote(provenance["source_domain"]),
            document_type=sql_quote(provenance["document_type"]),
            evidence_text=sql_quote(provenance["evidence_text"]),
            confidence=sql_quote(claim["confidence"]),
            conflict_status=sql_quote(claim.get("conflict_status", "")),
            conflict_reason=sql_quote(claim["conflict_reason"]) if claim.get("conflict_reason") else "NULL",
            status=sql_quote(claim["status"]),
        ).strip()
        return [source_insert, claim_insert]

    def _sql_for_durable_claim(self, claim: Dict[str, Any]) -> str:
        return """
INSERT INTO durable_claims (
  durable_claim_id, source_claim_id, run_id, entity_type, entity_name,
  parameter_id, attribute, attribute_subtype, promotion_decision, claim_text, value_json,
  location_scope_json, source_geo_scope_json, geo_evidence_json, time_scope_json, provenance_json, confidence, status
) VALUES (
  {durable_claim_id}, {source_claim_id}, {run_id}, {entity_type}, {entity_name},
  {parameter_id}, {attribute}, {attribute_subtype}, {promotion_decision}, {claim_text}, {value_json}::jsonb,
  {location_scope_json}::jsonb, {source_geo_scope_json}::jsonb, {geo_evidence_json}::jsonb, {time_scope_json}::jsonb, {provenance_json}::jsonb, {confidence}, {status}
)
ON CONFLICT (durable_claim_id) DO UPDATE SET
  source_claim_id = EXCLUDED.source_claim_id,
  run_id = EXCLUDED.run_id,
  entity_type = EXCLUDED.entity_type,
  entity_name = EXCLUDED.entity_name,
  parameter_id = EXCLUDED.parameter_id,
  attribute = EXCLUDED.attribute,
  attribute_subtype = EXCLUDED.attribute_subtype,
  promotion_decision = EXCLUDED.promotion_decision,
  claim_text = EXCLUDED.claim_text,
  value_json = EXCLUDED.value_json,
  location_scope_json = EXCLUDED.location_scope_json,
  source_geo_scope_json = EXCLUDED.source_geo_scope_json,
  geo_evidence_json = EXCLUDED.geo_evidence_json,
  time_scope_json = EXCLUDED.time_scope_json,
  provenance_json = EXCLUDED.provenance_json,
  confidence = EXCLUDED.confidence,
  status = EXCLUDED.status;
""".format(
            durable_claim_id=sql_quote(claim["durable_claim_id"]),
            source_claim_id=sql_quote(claim["source_claim_id"]),
            run_id=sql_quote(claim["run_id"]),
            entity_type=sql_quote(claim["entity"]["entity_type"]),
            entity_name=sql_quote(claim["entity"]["name"]),
            parameter_id=sql_quote(claim["parameter_id"]),
            attribute=sql_quote(claim["attribute"]),
            attribute_subtype=sql_quote(claim["attribute_subtype"]),
            promotion_decision=sql_quote(claim["promotion_decision"]),
            claim_text=sql_quote(claim["claim_text"]),
            value_json=sql_quote(json.dumps(claim["value"], sort_keys=True)),
            location_scope_json=sql_quote(json.dumps(claim["location_scope"], sort_keys=True)),
            source_geo_scope_json=sql_quote(json.dumps(claim.get("source_geo_scope", {"level": "global", "name": "global"}), sort_keys=True)),
            geo_evidence_json=sql_quote(
                json.dumps(
                    claim.get(
                        "geo_evidence",
                        {
                            "claim_location_source": "default_global",
                            "claim_location_confidence": "none",
                            "claim_location_text": "",
                            "source_location_source": "default_global",
                            "source_location_confidence": "none",
                            "source_location_text": "",
                            "matched_locations": [],
                        },
                    ),
                    sort_keys=True,
                )
            ),
            time_scope_json=sql_quote(json.dumps(claim["time_scope"], sort_keys=True)),
            provenance_json=sql_quote(json.dumps(claim["provenance"], sort_keys=True)),
            confidence=sql_quote(claim["confidence"]),
            status=sql_quote(claim["status"]),
        ).strip()

    def _load_json(self, path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)


def sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_nullable_number(value: Any) -> str:
    if value is None:
        return "NULL"
    return str(value)


def sql_json_or_null(value: Any) -> str:
    if value is None:
        return "NULL"
    return sql_quote(json.dumps(value, sort_keys=True)) + "::jsonb"


def centroid_lat(scope: Dict[str, Any]) -> Any:
    return scope.get("centroid", {}).get("lat")


def centroid_lon(scope: Dict[str, Any]) -> Any:
    return scope.get("centroid", {}).get("lon")
