SET search_path = cv_analyzer;

ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS semantic_similarity_score NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS semantic_threshold        NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS matched_skills            JSONB,
    ADD COLUMN IF NOT EXISTS missing_skills            JSONB,
    ADD COLUMN IF NOT EXISTS matched_keywords          JSONB,
    ADD COLUMN IF NOT EXISTS missing_keywords          JSONB,
    ADD COLUMN IF NOT EXISTS gatekeeper_reason_json    JSONB;

COMMENT ON COLUMN applications.semantic_similarity_score IS 'Level 1: cosine similarity % between CV and criteria text';
COMMENT ON COLUMN applications.semantic_threshold IS 'Level 1: threshold % used when this application was scored';
COMMENT ON COLUMN applications.matched_skills IS 'Level 1: skills/certifications matched in CV';
COMMENT ON COLUMN applications.missing_skills IS 'Level 1: skills/certifications not found in CV';
COMMENT ON COLUMN applications.matched_keywords IS 'Level 1: broader criteria keywords matched in CV';
COMMENT ON COLUMN applications.missing_keywords IS 'Level 1: broader criteria keywords not found in CV';
COMMENT ON COLUMN applications.gatekeeper_reason_json IS 'Level 1: full diagnostic object (scores, counts, override reason)';
