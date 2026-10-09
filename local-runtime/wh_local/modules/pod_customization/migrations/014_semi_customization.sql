-- 半定制模式标记。历史批次默认 full（全定制），半定制批次写 semi。
-- 只加一列，不重建表：模板三列（template_id/template_snapshot_id/template_name）
-- 的 NOT NULL + FK 约束由惰性占位模板（ensure_semi_placeholder）满足。
ALTER TABLE pod_customization_batches ADD COLUMN mode TEXT NOT NULL DEFAULT 'full';
