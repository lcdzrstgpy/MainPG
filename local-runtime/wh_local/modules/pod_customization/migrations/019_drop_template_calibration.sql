-- 移除模板标定（蒙版/锚点）能力。
-- 模板照片现在只作为生图参考图使用，标定数据不再参与任何链路。
-- 001 当时创建了 calibration_status / calibration_json 两列（历史记录保持不变），
-- 本迁移把它们从存量库真正删除。重放安全由 _apply_019 逐列判断存在性后执行。
ALTER TABLE pod_customization_templates DROP COLUMN calibration_status;
ALTER TABLE pod_customization_templates DROP COLUMN calibration_json;
ALTER TABLE pod_customization_template_snapshots DROP COLUMN calibration_json;
