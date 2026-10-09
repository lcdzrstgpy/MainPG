ALTER TABLE data_collection_plugin_sessions
    ADD COLUMN client_instance_id TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_data_collection_plugin_sessions_owner_client
    ON data_collection_plugin_sessions(actor_id, workspace_id, client_instance_id);
