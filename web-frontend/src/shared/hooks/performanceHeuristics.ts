/**
 * 「流畅模式」自动评估的纯函数逻辑（不碰 DOM，方便单测）。
 *
 * 判断分两层：
 *  - 设备信息：CPU 核心数（navigator.hardwareConcurrency）与内存档位
 *    （navigator.deviceMemory，仅 Chromium 系提供，Firefox/Safari 拿不到）。
 *  - 实际帧率：启动稳定后采样一段 rAF 帧率，明显掉帧才算「渲染慢」——
 *    这一层专门覆盖「配置看着不差，但显卡老旧/浏览器硬件加速失效」的机器。
 *
 * 判定刻意保守：宁漏勿误。只有证据明确时才自动开启，其余情况留给用户
 * 在偏好设置里手动打开。
 */

/** 平均帧率低于该值视为「渲染慢」（60Hz 屏满帧约 60fps）。 */
export const WEAK_FRAME_RATE_THRESHOLD = 40;

/** 帧率采样窗口时长（毫秒）。 */
export const FRAME_RATE_SAMPLE_MS = 2000;

/** 采样至少要有多少帧才可信（太少说明期间页面被切走或几乎停摆）。 */
export const MIN_SAMPLE_FRAMES = 8;

/** 核心数达到该值视为「明显不弱」：帧率异常时也先不自动降级，防异常采样误伤。 */
export const STRONG_CORES = 8;

export type DeviceFacts = {
  /** navigator.hardwareConcurrency；拿不到时为 0。 */
  cores: number;
  /** navigator.deviceMemory 的近似内存档位（GB）；浏览器不给时为 undefined。 */
  memoryGb?: number;
};

/** 设备信息是否明确偏弱（≤2 核，或 ≤4 核且内存 ≤4G）。 */
export function deviceLooksWeak({ cores, memoryGb }: DeviceFacts): boolean {
  if (cores <= 0) return false;
  if (cores <= 2) return true;
  return cores <= 4 && typeof memoryGb === "number" && memoryGb > 0 && memoryGb <= 4;
}

/** 设备信息是否明确不弱（≥8 核）。 */
export function deviceLooksStrong({ cores }: DeviceFacts): boolean {
  return cores >= STRONG_CORES;
}

/** 帧率是否低到值得自动降级（null = 采样不可信，不做判断）。 */
export function isFrameRateWeak(fps: number | null): boolean {
  return fps !== null && fps < WEAK_FRAME_RATE_THRESHOLD;
}

/**
 * 由「帧数 + 首末帧时间差」折算平均帧率。
 * 帧数过少（不足 MIN_SAMPLE_FRAMES）或时间差过短时返回 null，宁可不判。
 */
export function computeFrameRate(frameCount: number, elapsedMs: number): number | null {
  if (frameCount < MIN_SAMPLE_FRAMES || elapsedMs < 1000) return null;
  return ((frameCount - 1) * 1000) / elapsedMs;
}
