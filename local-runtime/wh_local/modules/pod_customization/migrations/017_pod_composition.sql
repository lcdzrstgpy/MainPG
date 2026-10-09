-- POD 全定制「构图/视角定制」：按账号+工作区只保留最新一份用户创作的构图。
-- 四格位置与角色固定（左主图 / 右上细节A / 左下细节B / 右下素材），用户只决定每格怎么拍；
-- 生图时用它替换原本写死的四格构图/角度描述，仅保留硬约束。
--
-- panels_json 形状：{"panel_1": "...", "panel_2": "...", "panel_3": "...", "panel_4": "..."}
-- 唯一索引保证每个 (workspace_id, owner_user_id) 只有一份「最新」。
CREATE TABLE IF NOT EXISTS pod_customization_compositions (
    composition_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    raw_input TEXT NOT NULL DEFAULT '',
    panels_json TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pod_compositions_owner
    ON pod_customization_compositions (workspace_id, owner_user_id);

-- 批次冻结该批次实际套用的构图快照（可复现 + 前端可展示）；未套用最新构图的历史/新批次为空串。
ALTER TABLE pod_customization_batches ADD COLUMN composition_json TEXT NOT NULL DEFAULT '';
