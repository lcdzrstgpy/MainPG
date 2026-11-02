-- 跨采集路径去重（2026-11-02）：插件单品「采集到工作台」与选品确认入池
-- 会为同一个商品页各留一条草稿 —— 两条路的 candidate_id 前缀不同
-- （`plugin:<platform>:<id|url>` vs `1688:<offer_id>`），永远对不上。
-- 同一个商品页规范化后的 URL（source_ref）才是可靠的跨路径身份。
--
-- 这里只加**查询索引**，不加唯一约束：
-- 历史库可能已经存在同 source_ref 的重复（选品「每个 run 一条」是刻意行为），
-- 建唯一索引会让迁移在用户库上直接失败 → 应用起不来。
CREATE INDEX IF NOT EXISTS idx_product_processing_drafts_source_ref
    ON product_processing_drafts (workspace_id, source_ref);
