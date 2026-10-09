-- 工作区级核价初筛配置：插件采集的数据入库后先按此条件过滤，再进入 STEP 02 人工确认。
CREATE TABLE IF NOT EXISTS price_verification_prescreen_settings (
    workspace_id TEXT PRIMARY KEY,
    min_adjusted_price_cny TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT ''
);
