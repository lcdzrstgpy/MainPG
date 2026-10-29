CREATE TABLE IF NOT EXISTS data_collection_plugin_devices (
    device_id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    browser TEXT NOT NULL DEFAULT '',
    extension_version TEXT NOT NULL DEFAULT '',
    token_hash TEXT NOT NULL UNIQUE,
    allowed_shops_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_data_collection_plugin_devices_actor
    ON data_collection_plugin_devices(actor_id, workspace_id);

CREATE TABLE IF NOT EXISTS data_collection_plugin_pair_codes (
    code TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    used_at TEXT NOT NULL DEFAULT ''
);
