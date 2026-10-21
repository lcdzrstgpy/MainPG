/**
 * 抽取自 integrations/clipforge/src/lib/video-composer/qc.ts 的 `QcReport` / `QcCheck`
 * （原文件第 21–28、57–63 行），只保留 repairPlanFromQc 需要的字段。
 * 只读同步：结构以 ClipForge 上游为准；修改请先改上游再同步，禁止在本文件单向漂移。
 *
 * 只抽取最小结构，不引入 video-composer 实现（不在第一批复制范围）。
 */

export interface QcReportLike {
  checks: Array<{ id: string; level: string }>;
}
