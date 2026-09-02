-- User-controlled Dianxiaomi export selection.  Missing rows intentionally
-- mean selected so batches created before this migration retain their behavior.
CREATE TABLE IF NOT EXISTS pod_customization_style_export_selection (
    batch_id TEXT NOT NULL,
    style_index INTEGER NOT NULL CHECK (style_index >= 1),
    selected INTEGER NOT NULL DEFAULT 1 CHECK (selected IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (batch_id, style_index),
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE
);
