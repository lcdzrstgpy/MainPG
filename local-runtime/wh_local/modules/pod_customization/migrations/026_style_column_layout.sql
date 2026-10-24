-- 两级批次 + 按角色列式生成（方案：docs/superpowers/specs/2026-10-22-pod-style-column-generation-design.md）。
--
-- L1（pod_customization_batches）继续承担款数、整批风格与计费；这里只新增布局标记与
-- 冻结元数据。历史批次 generation_layout 保持 'legacy'，执行路径完全不变。
--
-- L2（style_groups）固定 4 款；group_sheets 是「组 × 角色（轮次）」的执行单元，
-- 每行对应一张 2×2 母图。四格面板仍写 pod_customization_style_grid_results，
-- 前端与导出链路零改动。

ALTER TABLE pod_customization_batches ADD COLUMN generation_layout TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE pod_customization_batches ADD COLUMN group_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE pod_customization_batches ADD COLUMN generation_meta_json TEXT NOT NULL DEFAULT '';
ALTER TABLE pod_customization_batches ADD COLUMN art_direction_json TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS pod_customization_style_groups (
    group_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    group_index INTEGER NOT NULL,
    style_indices_json TEXT NOT NULL,
    status TEXT NOT NULL,
    current_round TEXT NOT NULL DEFAULT '',
    execution_epoch INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (batch_id, group_index),
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pod_customization_group_sheets (
    sheet_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL,
    grid_asset_id TEXT NOT NULL DEFAULT '',
    attempt INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT '',
    prompt_snapshot TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (group_id, role),
    FOREIGN KEY (group_id) REFERENCES pod_customization_style_groups (group_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pod_group_sheets_batch
    ON pod_customization_group_sheets (batch_id, role);

-- 构图模板语义从「一张图的四格」改为「四次调用的四轮」。键与存储结构不变，
-- 这里只记录来源版本用于归因：panel_v1 = 旧四格口径，role_v1 = 四轮口径。
ALTER TABLE pod_customization_compositions ADD COLUMN layout TEXT NOT NULL DEFAULT 'panel_v1';
