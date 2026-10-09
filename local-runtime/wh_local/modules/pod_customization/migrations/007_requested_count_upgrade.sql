-- Forward-only upgrade for databases whose published POD 001 restricted
-- requested_count to 20/40/100.  Historical migration 001 remains immutable.
PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

DROP TABLE IF EXISTS pod_customization_batches_requested_count_v2;

CREATE TABLE pod_customization_batches_requested_count_v2 (
    batch_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    template_id TEXT NOT NULL,
    template_snapshot_id TEXT NOT NULL,
    template_name TEXT NOT NULL,
    requested_count INTEGER NOT NULL CHECK (requested_count BETWEEN 1 AND 200),
    processed_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    initial_call_count INTEGER NOT NULL,
    refill_call_count INTEGER NOT NULL DEFAULT 0,
    max_refill_calls INTEGER NOT NULL,
    prompt_version TEXT NOT NULL,
    prompt_snapshot TEXT NOT NULL,
    business_fields_json TEXT NOT NULL,
    creative_prompt TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    listing_fields_json TEXT NOT NULL DEFAULT 'null',
    FOREIGN KEY (template_id) REFERENCES pod_customization_templates (template_id),
    FOREIGN KEY (template_snapshot_id) REFERENCES pod_customization_template_snapshots (snapshot_id)
);

INSERT INTO pod_customization_batches_requested_count_v2 (
    batch_id, workspace_id, owner_user_id, title, status, template_id,
    template_snapshot_id, template_name, requested_count, processed_count,
    completed_count, failed_count, initial_call_count, refill_call_count,
    max_refill_calls, prompt_version, prompt_snapshot, business_fields_json,
    creative_prompt, error_message, created_at, updated_at, started_at,
    finished_at, listing_fields_json
)
SELECT
    batch_id, workspace_id, owner_user_id, title, status, template_id,
    template_snapshot_id, template_name, requested_count, processed_count,
    completed_count, failed_count, initial_call_count, refill_call_count,
    max_refill_calls, prompt_version, prompt_snapshot, business_fields_json,
    creative_prompt, error_message, created_at, updated_at, started_at,
    finished_at, listing_fields_json
FROM pod_customization_batches;

DROP TABLE pod_customization_batches;
ALTER TABLE pod_customization_batches_requested_count_v2
    RENAME TO pod_customization_batches;

CREATE INDEX idx_pod_customization_batches_owner
    ON pod_customization_batches (workspace_id, owner_user_id, created_at DESC);
CREATE INDEX idx_pod_customization_batches_status
    ON pod_customization_batches (status, updated_at);

COMMIT;

PRAGMA foreign_keys = ON;
