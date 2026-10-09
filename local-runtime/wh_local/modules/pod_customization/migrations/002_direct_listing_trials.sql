-- A synchronous single-product experiment, intentionally separate from queued 20/40/100 batch work.
CREATE TABLE IF NOT EXISTS pod_customization_direct_listing_trials (
    trial_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    template_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
    prompt_snapshot TEXT NOT NULL,
    grid_attempt_asset_ids_json TEXT NOT NULL,
    panel_asset_ids_json TEXT NOT NULL DEFAULT '{}',
    public_urls_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (template_id) REFERENCES pod_customization_templates (template_id)
);

CREATE INDEX IF NOT EXISTS idx_pod_direct_listing_trials_owner
    ON pod_customization_direct_listing_trials (workspace_id, owner_user_id, created_at DESC);
