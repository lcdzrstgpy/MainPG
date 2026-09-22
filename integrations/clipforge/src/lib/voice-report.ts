/**
 * 逐镜音频来源报告 — 合成阶段的可解释性归约（设计文档 §9）。
 *
 * 为什么：合成默认不阻断 TTS 失败，降级与失败只落进 `.timeline.json` 的 `warnings`
 * 数组，工具和用户都看不出「这一镜的声音到底是谁出的」。这里把每镜实际音源
 * （volcengine 付费 / edge 免费 / native 素材自带 / none 无音轨）与降级原因归约成
 * 稳定、可断言的汇总，供 sidecar 消费，并为后续「失败即待处理」阶段留出数据基础。
 *
 * 纯函数：不触 FFmpeg、不读库、不抛错；默认行为是「只记录不阻断」。
 */

/** 单个镜头的实际音源：付费 TTS / 免费 Edge / 素材原生音轨 / 无音轨（或失败） */
export type VoiceSource = "volcengine" | "edge" | "native" | "none";

/** 既有降级语义：付费失败回退免费 / 整体失败静音 */
export type VoiceWarningCode = "tts_fallback_free" | "tts_failed";

export interface VoiceWarning {
  code: VoiceWarningCode;
  shotId: number;
}

export interface VoiceReportShot {
  shotId: number;
  source: VoiceSource;
  /** 降级/失败的可读原因；音源正常时为 undefined */
  reason?: string;
  /** 该镜关联的警告码；无警告时为 undefined */
  warning?: VoiceWarningCode;
}

export interface VoiceReport {
  /** 与 timeline.json 其它字段一同演进的报告版本 */
  version: 1;
  shots: VoiceReportShot[];
  counts: Record<VoiceSource, number>;
  /** 是否出现降级或失败（tts_fallback_free / tts_failed） */
  hasFailures: boolean;
  /** 出现警告的镜头 id（去重，按警告出现顺序） */
  failedShotIds: number[];
}

/** 把「每镜音源 + 警告」归约为 sidecar 可断言的汇总统计。 */
export function buildVoiceReport(input: {
  shots: Array<{ shotId: number; source: VoiceSource; reason?: string }>;
  warnings?: VoiceWarning[];
}): VoiceReport {
  const warnings = input.warnings ?? [];
  const warningByShot = new Map<number, VoiceWarningCode>();
  const failedShotIds: number[] = [];
  for (const w of warnings) {
    if (!warningByShot.has(w.shotId)) failedShotIds.push(w.shotId);
    warningByShot.set(w.shotId, w.code);
  }

  const counts: Record<VoiceSource, number> = { volcengine: 0, edge: 0, native: 0, none: 0 };
  const shots: VoiceReportShot[] = input.shots.map((s) => {
    if (s.source in counts) counts[s.source] += 1;
    const warning = warningByShot.get(s.shotId);
    return {
      shotId: s.shotId,
      source: s.source,
      ...(s.reason ? { reason: s.reason } : {}),
      ...(warning ? { warning } : {}),
    };
  });

  return {
    version: 1,
    shots,
    counts,
    hasFailures: warnings.length > 0,
    failedShotIds,
  };
}
