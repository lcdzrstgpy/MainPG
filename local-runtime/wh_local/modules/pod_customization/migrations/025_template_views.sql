-- 模板结构视图集 + 结构事实表。
--
-- 背景：全定制原先只有一张主模板图。透明 / 亮面 / 细结构产品在单视角下无法
-- 提供足够的几何证据，模型只能按先验补全，导致层数、部件数、比例畸变。
-- 这里把模板升级为「1 张主图 + 0-3 张结构视图」，并在上传时把视图集转成
-- 可校验的结构事实表（部件数 / 层数 / 板厚 / 材质透明度…），以文字硬约束
-- 注入生成提示词。
--
-- 视图角色：primary（主图，必填）/ side（侧视）/ top（俯视）/ dimension（尺寸图，
-- 仅用于分析，不参与生图）。主图仍存放在 templates.asset_id，views 表只记录
-- 额外的结构视图，避免一次性改写既有模板表语义。

ALTER TABLE pod_customization_templates ADD COLUMN structure_facts_json TEXT NOT NULL DEFAULT '';
ALTER TABLE pod_customization_templates ADD COLUMN structure_status TEXT NOT NULL DEFAULT 'none';
ALTER TABLE pod_customization_templates ADD COLUMN structure_analysis_version TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS pod_customization_template_views (
    view_id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (template_id, role),
    FOREIGN KEY (template_id) REFERENCES pod_customization_templates (template_id),
    FOREIGN KEY (asset_id) REFERENCES pod_customization_assets (asset_id)
);

CREATE INDEX IF NOT EXISTS idx_pod_template_views_owner
    ON pod_customization_template_views (workspace_id, owner_user_id, template_id);
