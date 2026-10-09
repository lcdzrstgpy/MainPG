CREATE TABLE IF NOT EXISTS daily_selection_sku_repull_outbox (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'completed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL DEFAULT (datetime('now')),
    claim_token TEXT NOT NULL DEFAULT '',
    claimed_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (workspace_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_daily_selection_sku_repull_outbox_pending
    ON daily_selection_sku_repull_outbox (status, available_at, created_at);
