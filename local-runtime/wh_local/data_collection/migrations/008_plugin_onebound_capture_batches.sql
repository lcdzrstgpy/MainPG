CREATE TABLE IF NOT EXISTS plugin_onebound_capture_batches (
    batch_id TEXT PRIMARY KEY,
    parent_batch_id TEXT NOT NULL DEFAULT '',
    workspace_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    page_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'prepared' CHECK (status IN ('prepared','queued','running','completed','partial','cancelled','failed','expired')),
    cancelled INTEGER NOT NULL DEFAULT 0 CHECK (cancelled IN (0,1)),
    created_count INTEGER NOT NULL DEFAULT 0 CHECK (created_count >= 0),
    refreshed_count INTEGER NOT NULL DEFAULT 0 CHECK (refreshed_count >= 0),
    skipped_count INTEGER NOT NULL DEFAULT 0 CHECK (skipped_count >= 0),
    failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    unprocessed_count INTEGER NOT NULL DEFAULT 0 CHECK (unprocessed_count >= 0),
    total_count INTEGER NOT NULL DEFAULT 0 CHECK (total_count >= 0),
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_plugin_onebound_capture_batches_workspace_created
    ON plugin_onebound_capture_batches (workspace_id, created_at DESC, batch_id);

CREATE TABLE IF NOT EXISTS plugin_onebound_capture_items (
    batch_id TEXT NOT NULL,
    offer_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','succeeded','skipped','failed','unprocessed')),
    outcome TEXT NOT NULL DEFAULT '' CHECK (outcome IN ('','created','refreshed','skipped','failed','unprocessed')),
    draft_id INTEGER,
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (batch_id, offer_id),
    FOREIGN KEY (batch_id) REFERENCES plugin_onebound_capture_batches(batch_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_plugin_onebound_capture_items_batch_status
    ON plugin_onebound_capture_items (batch_id, status, offer_id);
