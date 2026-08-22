-- 款式级手动重试次数：整款重生成与标题重生成共享免费额度，
-- 超过免费上限（默认 2 次）后需用户确认付费重试（无论成败均按款式价扣费）。
CREATE TABLE IF NOT EXISTS pod_customization_style_retries (
    batch_id TEXT NOT NULL,
    style_index INTEGER NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (batch_id, style_index)
);
