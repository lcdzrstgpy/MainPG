-- Secret-free audit records for successful POD workbook exports.
-- File bytes and provider/customer credentials must never be stored here.
CREATE TABLE IF NOT EXISTS pod_customization_export_records (
    export_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    format TEXT NOT NULL,
    exported_count INTEGER NOT NULL CHECK (exported_count >= 0),
    skipped_count INTEGER NOT NULL CHECK (skipped_count >= 0),
    created_at TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pod_export_records_owner_batch_created
    ON pod_customization_export_records (
        workspace_id,
        owner_user_id,
        batch_id,
        created_at DESC,
        export_id DESC
    );
