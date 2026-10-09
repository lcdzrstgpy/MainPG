CREATE TABLE IF NOT EXISTS shop_collection_batches (
    batch_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    shop_sid TEXT NOT NULL,
    seed_offer_id TEXT NOT NULL DEFAULT '',
    shop_url TEXT NOT NULL DEFAULT '',
    shop_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','resolving','listing','enriching','pausing','paused','cancelling','cancelled','completed','partial','failed')),
    next_page INTEGER NOT NULL DEFAULT 1,
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    max_pages INTEGER NOT NULL DEFAULT 100 CHECK (max_pages BETWEEN 1 AND 100),
    listing_complete INTEGER NOT NULL DEFAULT 0,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    missing_id_count INTEGER NOT NULL DEFAULT 0,
    succeeded_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    created_count INTEGER NOT NULL DEFAULT 0,
    refreshed_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    lease_owner TEXT NOT NULL DEFAULT '',
    lease_expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_shop_collection_active_shop
    ON shop_collection_batches (workspace_id, shop_sid)
    WHERE status IN ('queued','resolving','listing','enriching','pausing','paused','cancelling');

CREATE INDEX IF NOT EXISTS idx_shop_collection_batches_workspace_created
    ON shop_collection_batches (workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS shop_collection_items (
    item_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    offer_id TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    source_title TEXT NOT NULL DEFAULT '',
    detail_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (detail_status IN ('pending','running','succeeded','failed','cancelled')),
    intake_action TEXT NOT NULL DEFAULT 'none'
        CHECK (intake_action IN ('none','created','refreshed','skipped')),
    attempts INTEGER NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    candidate_json TEXT NOT NULL DEFAULT '{}',
    lease_owner TEXT NOT NULL DEFAULT '',
    lease_expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    UNIQUE (batch_id, offer_id),
    FOREIGN KEY (batch_id) REFERENCES shop_collection_batches (batch_id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_shop_collection_items_batch_status
    ON shop_collection_items (batch_id, detail_status, created_at);

CREATE TABLE IF NOT EXISTS shop_collection_api_calls (
    call_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('item_search_shop','item_get')),
    reservation_granted INTEGER NOT NULL CHECK (reservation_granted IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (batch_id) REFERENCES shop_collection_batches (batch_id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_shop_collection_api_calls_batch_operation
    ON shop_collection_api_calls (batch_id, operation, call_id);
