-- POD 批次按款排查日志表：append-only 事件流，补足「原地更新看不到状态流转」的缺口。
-- 只追加、不参与生成/导出逻辑；随批次删除靠 ON DELETE CASCADE 级联清理。
--
-- event 语义：
--   batch_status          批次整体状态变化（style_index=0，status=新状态）
--   style_grid_status     每款每图状态变化（style_index + variant_index + status）
--   style_title_status    每款标题状态变化（style_index + status）
--   spec_card_reprint     规格卡按款重印
--   style_regenerate      整款重生成
--   style_title_regenerate 单款标题重生成
--   batch_retry           批次失败款重试
--   pause_requested       请求暂停
--   cancel_requested      请求取消
-- error 只存截断后的错误摘要（写入侧限制 ≤500 字符），避免表膨胀。
CREATE TABLE IF NOT EXISTS pod_customization_style_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    style_index INTEGER NOT NULL DEFAULT 0,
    variant_index INTEGER NOT NULL DEFAULT 0,
    event TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pod_style_events_batch
    ON pod_customization_style_events (batch_id, event_id);
