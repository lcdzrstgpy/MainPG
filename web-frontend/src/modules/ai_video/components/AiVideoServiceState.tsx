import type { ClipForgeStatus } from "../api/clipforgeApi";

type Props = {
  status: ClipForgeStatus | null;
  requestError: string;
  onStart: () => void;
};

const HEADINGS: Record<ClipForgeStatus["state"], string> = {
  unavailable: "AI 视频服务未安装完整",
  stopped: "AI 视频服务未启动",
  starting: "正在启动 AI 视频服务",
  ready: "AI 视频服务已就绪",
  failed: "AI 视频服务启动失败",
  stopping: "正在关闭 AI 视频服务",
};

// 不可内嵌（未安装/未启动/启动中/失败/关闭中/状态未知）时的统一落地页。
// 文案只讲用户能理解的状态；诊断编号是次要信息，进程退出码绝不进用户文案。
export function AiVideoServiceState({ status, requestError, onStart }: Props) {
  const state = status?.state ?? null;
  const retryable = state === "failed" && status?.error?.retryable === true;
  const startable = state === "stopped" || retryable;
  const heading = state ? HEADINGS[state] : "正在检查 AI 视频服务";
  const message = state === "failed"
    ? status?.error?.message || requestError || "AI 视频服务启动失败，请稍后重试。"
    : requestError || status?.message || "正在检查本地视频制作服务…";
  const diagnosticId = state === "failed" ? status?.error?.diagnosticId ?? null : null;
  const startLabel = state === "starting"
    ? "正在启动…"
    : state === "stopping"
      ? "正在关闭…"
      : state === "failed"
        ? "重新启动"
        : "启动 AI 视频服务";

  return (
    <section className={state === "failed" ? "ai-video-service-state is-error" : "ai-video-service-state"} aria-live="polite">
      <span className="ai-video-service-icon" aria-hidden="true">🎬</span>
      <div>
        <p className="ai-video-eyebrow">AI 创作工具</p>
        <h2>{heading}</h2>
        <p>{message}</p>
        <button type="button" onClick={onStart} disabled={!startable}>
          {startLabel}
        </button>
        {state === "unavailable" && (
          <p className="ai-video-service-help">请先构建并发布 integrations/clipforge 的 sidecar 产物。</p>
        )}
        {diagnosticId && <p className="ai-video-service-diagnostic">诊断编号：{diagnosticId}</p>}
      </div>
    </section>
  );
}
