-- 复刻方案支持用户手动编辑：记录最后一次手动保存时间。
-- 空串表示方案正文仍是 AI 原文；重新生成（force）会用新结果覆盖并清空该时间。
ALTER TABLE pod_customization_replica_transfer_plans ADD COLUMN edited_at TEXT NOT NULL DEFAULT '';
