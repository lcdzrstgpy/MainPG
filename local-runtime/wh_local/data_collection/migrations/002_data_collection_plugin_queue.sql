CREATE TABLE IF NOT EXISTS data_collection_plugin_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    session_token TEXT NOT NULL UNIQUE,
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'connected',
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_collection_plugin_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES data_collection_plugin_sessions(id) ON DELETE CASCADE,
    command_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_data_collection_plugin_commands_session_status_id
    ON data_collection_plugin_commands(session_id, status, id);
