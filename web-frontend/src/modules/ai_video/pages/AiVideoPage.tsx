import { useCallback, useEffect, useRef, useState } from "react";

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

// 外层二级导航允许的 href 取值，共 7 个模块入口。
export type AiVideoNavHref = (typeof AI_VIDEO_NAV)[number]["href"];

// 与内嵌 ClipForge 通信桥约定的冻结协议：
// 父 -> iframe 通知跳转；iframe -> 父 汇报自身当前路径。
export const AI_VIDEO_NAVIGATE_MESSAGE = "mainpg:ai-video-navigate";
export const AI_VIDEO_LOCATION_MESSAGE = "mainpg:ai-video-location";

// 外层导航状态：高亮项、iframe 是否已就绪（收到过第一条 location 消息）、ready 前最后一次点击的目标。
export type AiVideoNavState = {
  activeHref: AiVideoNavHref;
  ready: boolean;
  pendingHref: AiVideoNavHref | null;
};

// 一次状态迁移的结果：新状态，以及迁移后需要立即发给 iframe 的跳转目标。
export type AiVideoNavTransition = {
  state: AiVideoNavState;
  sendHref: AiVideoNavHref | null;
};

export const AI_VIDEO_NAV_INITIAL_STATE: AiVideoNavState = {
  activeHref: "/start",
  ready: false,
  pendingHref: null,
};

// iframe 的初始入口固定为「视频工作台」；外层导航只发消息，永远不改动 src。
const AI_VIDEO_INITIAL_HREF: AiVideoNavHref = "/start";

function embeddedClipForgeUrl(baseUrl: string, href: string) {
  const target = new URL(baseUrl);
  target.pathname = href;
  target.searchParams.set("embed", "mainpg");
  return target.toString();
}

// 从服务地址解析同源 origin，用于校验消息来源与指定 postMessage 目标；
// 解析失败返回 null，此时父侧既不发送也不校验。
export function clipForgeOrigin(rawUrl: string | null | undefined): string | null {
  if (!rawUrl) return null;
  try {
    return new URL(rawUrl).origin;
  } catch {
    return null;
  }
}

// 把内嵌 ClipForge 的真实路径映射为外层二级导航的高亮项：
// - 项目详情（/project/<id>/...）按归属映射为「我的项目」，不当作新的二级导航项；
// - /project/clone 是独立入口，必须优先精确匹配，不能被 /project/ 前缀规则吃掉；
// - 其余 7 个模块入口精确匹配；
// - 不在允许列表内的路径返回 null，表示保持当前高亮不变。
export function aiVideoNavHrefForPath(pathname: string): AiVideoNavHref | null {
  if (pathname === "/project/clone") return "/project/clone";
  if (pathname === "/projects" || pathname.startsWith("/project/")) return "/projects";
  const matched = AI_VIDEO_NAV.find((item) => item.href === pathname);
  return matched ? matched.href : null;
}

// 外层导航点击：立即乐观高亮；已就绪则立刻下发，未就绪则暂存等 ready 后补发。
export function selectAiVideoNav(state: AiVideoNavState, href: AiVideoNavHref): AiVideoNavTransition {
  if (state.ready) {
    return { state: { activeHref: href, ready: true, pendingHref: null }, sendHref: href };
  }
  return { state: { activeHref: href, ready: false, pendingHref: href }, sendHref: null };
}

// 收到 iframe 的 location 消息：第一条同时充当 ready 信号；
// 高亮以路径映射为准（映射不到则保持原值），并补发 ready 前暂存的最后一次点击。
// 暂存目标存在时保留它的乐观高亮：首条消息报的是 iframe 旧路径，若照搬会让刚点的项闪回旧项。
export function applyAiVideoLocation(state: AiVideoNavState, pathname: string): AiVideoNavTransition {
  const mapped = aiVideoNavHrefForPath(pathname);
  return {
    state: { activeHref: state.pendingHref ?? mapped ?? state.activeHref, ready: true, pendingHref: null },
    sendHref: state.pendingHref,
  };
}

// 向内嵌 iframe 发送跳转指令；拿不到 iframe 窗口或目标 origin 时不发送。
function postAiVideoNavigate(frame: HTMLIFrameElement | null, targetOrigin: string | null, href: AiVideoNavHref) {
  const target = frame?.contentWindow;
  if (!target || !targetOrigin) return;
  target.postMessage({ type: AI_VIDEO_NAVIGATE_MESSAGE, href }, targetOrigin);
}

export function AiVideoPage() {
  const [status, setStatus] = useState<ClipForgeStatus | null>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [navState, setNavState] = useState<AiVideoNavState>(AI_VIDEO_NAV_INITIAL_STATE);
  // iframe 常驻引用：外层导航只发消息，绝不销毁或重建 iframe。
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  // message 监听需要读到最新导航状态，用 ref 与 state 同步提交，避免把副作用塞进 setState 更新函数。
  const navStateRef = useRef<AiVideoNavState>(AI_VIDEO_NAV_INITIAL_STATE);
  const iframeOrigin = clipForgeOrigin(status?.url);

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

  // 统一提交状态迁移：写入 ref 与 state，并在需要时把跳转指令发给 iframe。
  const commitNavTransition = useCallback((transition: AiVideoNavTransition) => {
    navStateRef.current = transition.state;
    setNavState(transition.state);
    if (transition.sendHref) {
      postAiVideoNavigate(frameRef.current, iframeOrigin, transition.sendHref);
    }
  }, [iframeOrigin]);

  const selectHref = (href: AiVideoNavHref) => {
    commitNavTransition(selectAiVideoNav(navStateRef.current, href));
  };

  useEffect(() => {
    if (!iframeOrigin) return;
    const onMessage = (event: MessageEvent) => {
      // 只接受当前 iframe 从 clipforge 服务同源发出的 location 消息。
      if (event.origin !== iframeOrigin) return;
      if (event.source !== frameRef.current?.contentWindow) return;
      const data = event.data as { type?: unknown; pathname?: unknown } | null;
      if (!data || data.type !== AI_VIDEO_LOCATION_MESSAGE) return;
      if (typeof data.pathname !== "string") return;
      commitNavTransition(applyAiVideoLocation(navStateRef.current, data.pathname));
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [iframeOrigin, commitNavTransition]);

  if (status?.state === "ready" && status.url) {
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
              className={item.href === navState.activeHref ? "is-active" : ""}
              onClick={() => selectHref(item.href)}
            >
              <span aria-hidden="true">{item.icon}</span>
              {item.label}
            </button>
          ))}
        </nav>
        <iframe
          ref={frameRef}
          className="ai-video-frame"
          title="ClipForge AI 视频"
          src={embeddedClipForgeUrl(status.url, AI_VIDEO_INITIAL_HREF)}
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
