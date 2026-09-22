import { useEffect, useState } from "react";

import { getClipForgeStatus, startClipForge, type ClipForgeStatus } from "../api/clipforgeApi";

const AI_VIDEO_NAV = [
  { label: "视频工作台", href: "/start", icon: "✦" },
  { label: "我的项目", href: "/projects", icon: "▣" },
  { label: "商品素材", href: "/products", icon: "◇" },
  { label: "主播素材", href: "/presenters", icon: "♙" },
  { label: "爆款复刻", href: "/project/clone", icon: "↗" },
  { label: "任务中心", href: "/batch", icon: "◌" },
  { label: "设置", href: "/settings", icon: "⚙" },
] as const;

function embeddedClipForgeUrl(baseUrl: string, href: string) {
  const target = new URL(baseUrl);
  target.pathname = href;
  target.searchParams.set("embed", "mainpg");
  return target.toString();
}

export function AiVideoPage() {
  const [status, setStatus] = useState<ClipForgeStatus | null>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [selectedHref, setSelectedHref] = useState<(typeof AI_VIDEO_NAV)[number]["href"]>("/start");

  const refresh = () => {
    setError("");
    void getClipForgeStatus().then(setStatus).catch((cause: unknown) => {
      setError(cause instanceof Error ? cause.message : "无法读取 AI 视频服务状态");
    });
  };

  useEffect(refresh, []);

  const start = () => {
    setStarting(true);
    setError("");
    void startClipForge().then(setStatus).catch((cause: unknown) => {
      setError(cause instanceof Error ? cause.message : "AI 视频服务启动失败");
    }).finally(() => setStarting(false));
  };

  if (status?.state === "ready" && status.url) {
    const selected = AI_VIDEO_NAV.find((item) => item.href === selectedHref) ?? AI_VIDEO_NAV[0];
    return (
      <section className="ai-video-page" aria-label="AI 视频">
        <header className="ai-video-module-header">
          <div>
            <p className="ai-video-eyebrow">AI 创作工具</p>
            <h2>AI 视频制作</h2>
            <p>从商品素材到分镜、生成与导出，全部在界野工作台内完成。</p>
          </div>
          <span className="ai-video-service-badge">● 视频服务已连接</span>
        </header>
        <nav className="ai-video-module-nav" aria-label="AI 视频功能导航">
          {AI_VIDEO_NAV.map((item) => (
            <button
              key={item.href}
              type="button"
              className={item.href === selected.href ? "is-active" : ""}
              onClick={() => setSelectedHref(item.href)}
            >
              <span aria-hidden="true">{item.icon}</span>
              {item.label}
            </button>
          ))}
        </nav>
        <iframe
          key={selected.href}
          className="ai-video-frame"
          title="ClipForge AI 视频"
          src={embeddedClipForgeUrl(status.url, selected.href)}
        />
      </section>
    );
  }

  return (
    <section className="ai-video-service-state" aria-live="polite">
      <span className="ai-video-service-icon" aria-hidden="true">🎬</span>
      <div>
        <p className="ai-video-eyebrow">AI 创作工具</p>
        <h2>正在连接 AI 视频服务</h2>
        <p>{error || status?.message || "正在检查本地视频制作服务…"}</p>
        <button type="button" onClick={start} disabled={starting || status?.state === "unavailable"}>
          {starting ? "正在启动…" : "启动 AI 视频服务"}
        </button>
        {status?.state === "unavailable" && <p className="ai-video-service-help">请先构建 integrations/clipforge 的 standalone 服务。</p>}
      </div>
    </section>
  );
}
