CREATE TABLE IF NOT EXISTS ai_service_model_profiles (
    model_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    modes_json TEXT NOT NULL,
    reference_transport TEXT NOT NULL DEFAULT 'none',
    sizes_json TEXT NOT NULL DEFAULT '[]',
    default_count INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ai_service_templates (
    template_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL,
    default_count INTEGER NOT NULL DEFAULT 1,
    prompt TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ai_service_conversations (
    conversation_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_service_conversations_owner
    ON ai_service_conversations (workspace_id, owner_user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS ai_service_assets (
    asset_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    content_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_service_assets_owner
    ON ai_service_assets (workspace_id, owner_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ai_service_messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    asset_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_service_messages_conversation
    ON ai_service_messages (conversation_id, workspace_id, owner_user_id, created_at);

CREATE TABLE IF NOT EXISTS ai_service_creations (
    creation_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    request_json TEXT NOT NULL,
    output_asset_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_service_creations_owner
    ON ai_service_creations (workspace_id, owner_user_id, created_at DESC);
