-- 自由定制：逐张提示词。图片状态/资产仍写 style_grid_results（style_index=1, variant_index=1..6），
-- 本表只保存用户原文与第 1 张的参考图，避免把提示词塞进 JSON 快照后难以审计。
CREATE TABLE IF NOT EXISTS pod_customization_free_panels (
    batch_id TEXT NOT NULL,
    variant_index INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    reference_asset_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (batch_id, variant_index),
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pod_free_panels_batch
    ON pod_customization_free_panels (batch_id, variant_index);
