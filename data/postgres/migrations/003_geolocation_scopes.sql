ALTER TABLE normalized_claims
  ADD COLUMN IF NOT EXISTS location_geo_id TEXT;

ALTER TABLE normalized_claims
  ADD COLUMN IF NOT EXISTS location_centroid_lat DOUBLE PRECISION;

ALTER TABLE normalized_claims
  ADD COLUMN IF NOT EXISTS location_centroid_lon DOUBLE PRECISION;

ALTER TABLE normalized_claims
  ADD COLUMN IF NOT EXISTS location_bbox_json JSONB;

ALTER TABLE normalized_claims
  ADD COLUMN IF NOT EXISTS source_location_level TEXT DEFAULT 'global';

ALTER TABLE normalized_claims
  ADD COLUMN IF NOT EXISTS source_location_name TEXT DEFAULT 'global';

ALTER TABLE normalized_claims
  ADD COLUMN IF NOT EXISTS source_geo_id TEXT;

ALTER TABLE normalized_claims
  ADD COLUMN IF NOT EXISTS source_centroid_lat DOUBLE PRECISION;

ALTER TABLE normalized_claims
  ADD COLUMN IF NOT EXISTS source_centroid_lon DOUBLE PRECISION;

ALTER TABLE normalized_claims
  ADD COLUMN IF NOT EXISTS source_bbox_json JSONB;

ALTER TABLE normalized_claims
  ADD COLUMN IF NOT EXISTS source_geo_scope_json JSONB DEFAULT '{"level":"global","name":"global"}'::jsonb;

ALTER TABLE normalized_claims
  ADD COLUMN IF NOT EXISTS geo_evidence_json JSONB DEFAULT '{"claim_location_source":"default_global","claim_location_confidence":"none","claim_location_text":"","source_location_source":"default_global","source_location_confidence":"none","source_location_text":"","matched_locations":[]}'::jsonb;

ALTER TABLE normalized_claims
  ALTER COLUMN source_location_level SET NOT NULL;

ALTER TABLE normalized_claims
  ALTER COLUMN source_location_name SET NOT NULL;

ALTER TABLE normalized_claims
  ALTER COLUMN source_geo_scope_json SET NOT NULL;

ALTER TABLE normalized_claims
  ALTER COLUMN geo_evidence_json SET NOT NULL;

ALTER TABLE durable_claims
  ADD COLUMN IF NOT EXISTS source_geo_scope_json JSONB DEFAULT '{"level":"global","name":"global"}'::jsonb;

ALTER TABLE durable_claims
  ADD COLUMN IF NOT EXISTS geo_evidence_json JSONB DEFAULT '{"claim_location_source":"default_global","claim_location_confidence":"none","claim_location_text":"","source_location_source":"default_global","source_location_confidence":"none","source_location_text":"","matched_locations":[]}'::jsonb;

ALTER TABLE durable_claims
  ALTER COLUMN source_geo_scope_json SET NOT NULL;

ALTER TABLE durable_claims
  ALTER COLUMN geo_evidence_json SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_normalized_claims_source_scope
  ON normalized_claims (source_location_level, source_location_name);

CREATE INDEX IF NOT EXISTS idx_normalized_claims_location_geo_id
  ON normalized_claims (location_geo_id);

CREATE INDEX IF NOT EXISTS idx_normalized_claims_source_geo_id
  ON normalized_claims (source_geo_id);
