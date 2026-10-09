-- Direct shop intake owns a single replayable draft per workspace/candidate.
-- Handoff-created drafts remain outside this boundary because they retain a
-- non-null handoff_id as immutable confirmation history.
CREATE UNIQUE INDEX IF NOT EXISTS uq_product_processing_shop_candidate
    ON product_processing_drafts (workspace_id, candidate_id)
    WHERE source_type = 'onebound_api'
      AND handoff_id IS NULL
      AND candidate_id IS NOT NULL;
