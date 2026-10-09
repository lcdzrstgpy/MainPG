CREATE TABLE IF NOT EXISTS price_verification_quote_capture_batches (
    batch_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1)),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, batch_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_price_verification_quote_capture_batches_current
    ON price_verification_quote_capture_batches(workspace_id)
    WHERE is_current = 1;

CREATE INDEX IF NOT EXISTS idx_price_verification_quote_capture_batches_workspace_updated
    ON price_verification_quote_capture_batches(workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS price_verification_quote_capture_chunks (
    chunk_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    page_url TEXT NOT NULL DEFAULT '',
    item_count INTEGER NOT NULL CHECK (item_count BETWEEN 1 AND 5000),
    capture_json TEXT NOT NULL,
    items_json TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, batch_id, content_sha256),
    FOREIGN KEY (batch_id) REFERENCES price_verification_quote_capture_batches(batch_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_price_verification_quote_capture_chunks_batch
    ON price_verification_quote_capture_chunks(workspace_id, batch_id, created_at ASC);

CREATE TABLE IF NOT EXISTS price_verification_quote_capture_batch_snapshots (
    workspace_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    quote_run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, batch_id, revision),
    UNIQUE (workspace_id, quote_run_id),
    FOREIGN KEY (batch_id) REFERENCES price_verification_quote_capture_batches(batch_id)
        ON DELETE CASCADE,
    FOREIGN KEY (quote_run_id) REFERENCES price_verification_quote_runs(run_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_price_verification_quote_capture_batch_snapshots_batch
    ON price_verification_quote_capture_batch_snapshots(workspace_id, batch_id, revision DESC);
