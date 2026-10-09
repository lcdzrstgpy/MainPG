CREATE TABLE IF NOT EXISTS price_verification_pairing_codes (
    pairing_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    code_sha256 TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, code_sha256)
);
CREATE INDEX IF NOT EXISTS idx_price_verification_pairing_codes_workspace_expires
    ON price_verification_pairing_codes(workspace_id, expires_at);

CREATE TABLE IF NOT EXISTS price_verification_plugin_sessions (
    session_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    token_sha256 TEXT NOT NULL UNIQUE,
    browser TEXT NOT NULL,
    plugin_version TEXT NOT NULL DEFAULT '',
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'connected',
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_verification_plugin_sessions_workspace_seen
    ON price_verification_plugin_sessions(workspace_id, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS price_verification_plugin_commands (
    command_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, command_type, idempotency_key),
    FOREIGN KEY (session_id) REFERENCES price_verification_plugin_sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_price_verification_plugin_commands_workspace_status
    ON price_verification_plugin_commands(workspace_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS price_verification_provider_budgets (
    workspace_id TEXT NOT NULL,
    credential_fingerprint TEXT NOT NULL,
    shanghai_date TEXT NOT NULL,
    call_limit INTEGER NOT NULL,
    used_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, credential_fingerprint, shanghai_date)
);
CREATE INDEX IF NOT EXISTS idx_price_verification_provider_budgets_workspace_date
    ON price_verification_provider_budgets(workspace_id, shanghai_date);

CREATE TABLE IF NOT EXISTS price_verification_quote_runs (
    run_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    status TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    adapter_version TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_price_verification_quote_runs_workspace_created
    ON price_verification_quote_runs(workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS price_verification_quote_items (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    quote_key TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    PRIMARY KEY (workspace_id, run_id, quote_key),
    FOREIGN KEY (run_id) REFERENCES price_verification_quote_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_price_verification_quote_items_workspace_run
    ON price_verification_quote_items(workspace_id, run_id);

CREATE TABLE IF NOT EXISTS price_verification_sourcing_runs (
    run_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    quote_run_id TEXT NOT NULL,
    source_mode TEXT NOT NULL DEFAULT 'browser_image_search',
    status TEXT NOT NULL,
    task_count INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, run_id),
    FOREIGN KEY (quote_run_id) REFERENCES price_verification_quote_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_price_verification_sourcing_runs_workspace_created
    ON price_verification_sourcing_runs(workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS price_verification_source_candidates (
    workspace_id TEXT NOT NULL,
    sourcing_run_id TEXT NOT NULL,
    quote_key TEXT NOT NULL,
    candidate_key TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    PRIMARY KEY (workspace_id, sourcing_run_id, quote_key, candidate_key),
    FOREIGN KEY (sourcing_run_id) REFERENCES price_verification_sourcing_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_price_verification_source_candidates_workspace_run
    ON price_verification_source_candidates(workspace_id, sourcing_run_id);
