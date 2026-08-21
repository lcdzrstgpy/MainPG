-- Durable, secret-free POD billing execution ledger. Provider credentials and
-- remote session tokens remain memory-only and are deliberately absent here.
CREATE TABLE IF NOT EXISTS pod_customization_billing_runs (
    run_id TEXT PRIMARY KEY,
    action_key TEXT NOT NULL UNIQUE,
    action_type TEXT NOT NULL CHECK (action_type IN (
        'batch_initial', 'direct_trial', 'scene_optimization',
        'item_retry', 'style_retry', 'title_retry'
    )),
    target_id TEXT NOT NULL,
    batch_id TEXT NOT NULL DEFAULT '',
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    freeze_id TEXT NOT NULL,
    rule_version INTEGER NOT NULL,
    grant_expires_at TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    action_payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK (status IN (
        'authorized', 'resume_claimed', 'settling', 'settlement_pending', 'auth_required', 'settled'
    )),
    result_status TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    settled_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_pod_billing_runs_owner_status
    ON pod_customization_billing_runs (workspace_id, owner_user_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_pod_billing_runs_batch
    ON pod_customization_billing_runs (batch_id, workspace_id, owner_user_id, status);

CREATE TABLE IF NOT EXISTS pod_customization_billing_outcomes (
    run_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    feature TEXT NOT NULL CHECK (feature IN ('pod.title', 'pod.image')),
    status TEXT NOT NULL CHECK (status IN ('planned', 'started', 'success', 'no_return')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, call_id),
    FOREIGN KEY (run_id) REFERENCES pod_customization_billing_runs (run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pod_billing_outcomes_run_status
    ON pod_customization_billing_outcomes (run_id, status, call_id);
