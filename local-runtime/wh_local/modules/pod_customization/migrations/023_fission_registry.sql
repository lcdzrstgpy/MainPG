CREATE TABLE IF NOT EXISTS pod_customization_capability_registry_versions (
    version INTEGER PRIMARY KEY,
    content_hash TEXT NOT NULL UNIQUE,
    content_json TEXT NOT NULL,
    analysis_id TEXT NOT NULL DEFAULT '',
    input_sha256 TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pod_customization_rule_registry_versions (
    version INTEGER PRIMARY KEY,
    content_hash TEXT NOT NULL UNIQUE,
    content_json TEXT NOT NULL,
    analysis_id TEXT NOT NULL DEFAULT '',
    input_sha256 TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pod_customization_registry_current (
    registry_kind TEXT PRIMARY KEY CHECK (registry_kind IN ('capability', 'rule')),
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO pod_customization_registry_current (registry_kind, version)
VALUES ('capability', 0), ('rule', 0);

CREATE TABLE IF NOT EXISTS pod_customization_learning_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL DEFAULT '',
    style_index INTEGER NOT NULL DEFAULT 0,
    analysis_id TEXT NOT NULL DEFAULT '',
    input_sha256 TEXT NOT NULL DEFAULT '',
    candidate_json TEXT NOT NULL DEFAULT '{}',
    capability_version INTEGER NOT NULL DEFAULT 0,
    rule_version INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT '',
    failure_kind TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pod_fission_learning_versions
    ON pod_customization_learning_events (capability_version, rule_version, event_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_pod_fission_variant_outcome
    ON pod_customization_learning_events (batch_id, style_index, capability_version, rule_version)
    WHERE batch_id <> '' AND style_index > 0;

CREATE TRIGGER IF NOT EXISTS trg_fission_registry_capability_no_update
BEFORE UPDATE ON pod_customization_capability_registry_versions
BEGIN
    SELECT RAISE(ABORT, 'fission registry versions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_fission_registry_capability_no_delete
BEFORE DELETE ON pod_customization_capability_registry_versions
BEGIN
    SELECT RAISE(ABORT, 'fission registry versions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_fission_registry_rule_no_update
BEFORE UPDATE ON pod_customization_rule_registry_versions
BEGIN
    SELECT RAISE(ABORT, 'fission registry versions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_fission_registry_rule_no_delete
BEFORE DELETE ON pod_customization_rule_registry_versions
BEGIN
    SELECT RAISE(ABORT, 'fission registry versions are append-only');
END;
