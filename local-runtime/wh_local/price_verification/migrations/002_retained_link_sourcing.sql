CREATE TABLE IF NOT EXISTS price_verification_quote_decisions (
    decision_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    quote_run_id TEXT NOT NULL,
    quote_key TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('retained', 'rejected')),
    decided_by TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL,
    decided_at TEXT NOT NULL,
    UNIQUE (workspace_id, quote_run_id, quote_key, revision),
    FOREIGN KEY (workspace_id, quote_run_id, quote_key)
        REFERENCES price_verification_quote_items(workspace_id, run_id, quote_key)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_price_verification_quote_decisions_current
    ON price_verification_quote_decisions(workspace_id, quote_run_id, quote_key, revision DESC);

CREATE TABLE IF NOT EXISTS price_verification_sourcing_run_quotes (
    workspace_id TEXT NOT NULL,
    sourcing_run_id TEXT NOT NULL,
    quote_run_id TEXT NOT NULL,
    quote_key TEXT NOT NULL,
    official_link_url TEXT NOT NULL,
    main_image_url TEXT NOT NULL,
    selected_price_cny TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, sourcing_run_id, quote_key),
    FOREIGN KEY (workspace_id, sourcing_run_id)
        REFERENCES price_verification_sourcing_runs(workspace_id, run_id)
        ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, quote_run_id, quote_key)
        REFERENCES price_verification_quote_items(workspace_id, run_id, quote_key)
);

CREATE INDEX IF NOT EXISTS idx_price_verification_sourcing_run_quotes_quote
    ON price_verification_sourcing_run_quotes(workspace_id, quote_run_id, quote_key);
