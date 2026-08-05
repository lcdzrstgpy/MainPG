CREATE TABLE IF NOT EXISTS data_collection_plugin_command_requests (
    workspace_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    command_id INTEGER NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, command_type, idempotency_key),
    FOREIGN KEY (command_id) REFERENCES data_collection_plugin_commands(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_data_collection_plugin_command_requests_command
    ON data_collection_plugin_command_requests(command_id);
