-- 构图模板按模块分池：scope='full' 为全定制专用，scope='replica' 为爆款复刻专用。
-- 两者各自独立「生效」，互不覆盖——复刻生成/生效模板不再污染全定制的生效模板。
ALTER TABLE pod_customization_compositions ADD COLUMN scope TEXT NOT NULL DEFAULT 'full';

CREATE INDEX IF NOT EXISTS idx_pod_compositions_scope
    ON pod_customization_compositions (workspace_id, owner_user_id, scope);
