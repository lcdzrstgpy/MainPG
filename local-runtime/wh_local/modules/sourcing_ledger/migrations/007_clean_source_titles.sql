-- 清理历史货源标题里的脏数据：插件偶发把整块价格审核弹窗文字（含「采集来源」「移除」等）
-- 塞进 source_title，页面上会显示成一整段乱码式文本。这里统一清空，展示时回退到 offer_id。
UPDATE product_sourcing_sources
SET source_title = '', updated_at = datetime('now')
WHERE instr(source_title, '采集来源') > 0
   OR instr(source_title, '申请调整更新申报价格') > 0
   OR (instr(source_title, '移除') > 0 AND instr(source_title, 'SKU') > 0);
