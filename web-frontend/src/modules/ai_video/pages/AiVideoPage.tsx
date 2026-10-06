import { useCallback, useEffect, useRef, useState } from "react";

import { AiVideoServiceState } from "../components/AiVideoServiceState";
import { useClipForgeService } from "../hooks/useClipForgeService";
import { canEmbedClipForge, clipForgeInstanceKey } from "../state/clipforgeServiceState";

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

// uiMode 同步协议：父 -> iframe 下发界面模式；iframe -> 父 回报当前界面模式。
// uiMode 只允许 "simple"（小白模式）与 "pro"（导演模式）两种取值。
export const AI_VIDEO_UI_MODE_SET_MESSAGE = "mainpg:ai-video-ui-mode-set";
export const AI_VIDEO_UI_MODE_STATE_MESSAGE = "mainpg:ai-video-ui-mode-state";
// 独立存储键：专属于 AI 视频界面模式，不复用全局布局模式已占用的首页模式键。
export const AI_VIDEO_UI_MODE_STORAGE_KEY = "mainpg.aiVideo.uiMode";

export type AiVideoUiMode = "simple" | "pro";

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

// 服务就绪只代表后端 sidecar 在跑；iframe 要自己回报第一条可信 location 消息才算桥接成功。
// 超过该时限只标记「页面桥接超时」，绝不把后端服务判为失败。
const AI_VIDEO_BRIDGE_TIMEOUT_MS = 10000;

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

// 后端换了 sidecar 实例：旧 iframe 文档已作废，导航高亮与 bridge 就绪状态必须一并归零。
export function resetAiVideoNavForInstance(_state: AiVideoNavState): AiVideoNavState {
  return { ...AI_VIDEO_NAV_INITIAL_STATE };
}

// 严格白名单：只有 "simple" 与 "pro" 是合法界面模式，其余一律拒绝。
export function isAiVideoUiMode(value: unknown): value is AiVideoUiMode {
  return value === "simple" || value === "pro";
}

// 从持久化读到的原始值还原界面模式：白名单之外的任何值（含 null/空串）都回落为 "simple"。
export function aiVideoUiModeFromStored(raw: string | null | undefined): AiVideoUiMode {
  return isAiVideoUiMode(raw) ? raw : "simple";
}

// 向内嵌 iframe 发送跳转指令；拿不到 iframe 窗口或目标 origin 时不发送。
function postAiVideoNavigate(frame: HTMLIFrameElement | null, targetOrigin: string | null, href: AiVideoNavHref) {
  const target = frame?.contentWindow;
  if (!target || !targetOrigin) return;
  target.postMessage({ type: AI_VIDEO_NAVIGATE_MESSAGE, href }, targetOrigin);
}

// 向内嵌 iframe 下发界面模式；同样要求拿到 iframe 窗口与目标 origin，否则不发送。
function postAiVideoUiModeSet(frame: HTMLIFrameElement | null, targetOrigin: string | null, uiMode: AiVideoUiMode) {
  const target = frame?.contentWindow;
  if (!target || !targetOrigin) return;
  target.postMessage({ type: AI_VIDEO_UI_MODE_SET_MESSAGE, uiMode }, targetOrigin);
}

export function AiVideoPage() {
  // 服务健康状态来自真实轮询：服务退出或换实例后状态会自己变，不靠挂载时那一次请求。
  const { status, error, start } = useClipForgeService();
  const [bridgeTimedOut, setBridgeTimedOut] = useState(false);
  const [navState, setNavState] = useState<AiVideoNavState>(AI_VIDEO_NAV_INITIAL_STATE);
  // 界面模式：从本地持久化还原，读写失败（如隐私模式）时静默回落，绝不影响页面主流程。
  const [uiMode, setUiMode] = useState<AiVideoUiMode>(() => {
    try {
      return aiVideoUiModeFromStored(window.localStorage.getItem(AI_VIDEO_UI_MODE_STORAGE_KEY));
    } catch {
      return "simple";
    }
  });
  // iframe 常驻引用：外层导航只发消息，绝不销毁或重建 iframe。
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  // message 监听需要读到最新导航状态，用 ref 与 state 同步提交，避免把副作用塞进 setState 更新函数。
  const navStateRef = useRef<AiVideoNavState>(AI_VIDEO_NAV_INITIAL_STATE);
  // uiMode 权威下发标记：父侧成功下发过一次 set 之前，绝不吃 iframe 的本地模式回报，
  // 否则 iframe 会先回报自己 localStorage 里的模式、覆盖父页面保存的权威 uiMode。
  const uiModeSetSentRef = useRef(false);
  const iframeOrigin = clipForgeOrigin(status?.url);
  // 服务健康与 bridge 健康是两件事：前者看后端状态，后者只看 iframe 是否回报过可信 location。
  const instanceKey = clipForgeInstanceKey(status);
  const serviceReady = canEmbedClipForge(status);
  const bridgeReady = navState.ready;

  // 实例变化（新 instanceId 或新端口）意味着换了 sidecar 进程：旧 iframe 文档与导航态全部作废，
  // uiMode 下发标记也归零，等新 iframe 的第一次握手后再接受它的回报。
  useEffect(() => {
    const reset = resetAiVideoNavForInstance(navStateRef.current);
    navStateRef.current = reset;
    setNavState(reset);
    setBridgeTimedOut(false);
    uiModeSetSentRef.current = false;
  }, [instanceKey]);

  // 界面模式每一次变化都写回本地持久化；写失败（局部存储被禁用）时静默忽略，切换仍然可用。
  useEffect(() => {
    try {
      window.localStorage.setItem(AI_VIDEO_UI_MODE_STORAGE_KEY, uiMode);
    } catch {
      // localStorage 不可写：仅本次会话内生效，不打断用户操作。
    }
  }, [uiMode]);

  // 桥接就绪后把当前界面模式同步给 iframe；换实例后新 iframe 回报第一条 location 消息
  // （bridgeReady 重新变 true）会再次触发，从而把保存的模式重新下发。未就绪或拿不到 origin 时绝不发送。
  useEffect(() => {
    if (!bridgeReady || !iframeOrigin) return;
    postAiVideoUiModeSet(frameRef.current, iframeOrigin, uiMode);
    uiModeSetSentRef.current = true;
  }, [uiMode, bridgeReady, instanceKey, iframeOrigin]);

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
      // 只接受当前 iframe 从 clipforge 服务同源发出的消息。
      if (event.origin !== iframeOrigin) return;
      if (event.source !== frameRef.current?.contentWindow) return;
      const data = event.data as { type?: unknown; pathname?: unknown; uiMode?: unknown } | null;
      if (!data || data.type !== AI_VIDEO_LOCATION_MESSAGE) {
        // uiMode 回报：同样只信当前 iframe 同源消息、白名单校验通过、且父侧已完成第一次
        // 权威下发之后才落地——防止 iframe 挂载时的本地模式抢先覆盖父页面保存的模式。
        if (
          data &&
          data.type === AI_VIDEO_UI_MODE_STATE_MESSAGE &&
          isAiVideoUiMode(data.uiMode) &&
          uiModeSetSentRef.current
        ) {
          setUiMode(data.uiMode);
        }
        return;
      }
      if (typeof data.pathname !== "string") return;
      commitNavTransition(applyAiVideoLocation(navStateRef.current, data.pathname));
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [iframeOrigin, commitNavTransition]);

  // 服务已就绪但 iframe 还没回报第一条可信 location 消息：10 秒后只标记桥接超时。
  // bridge 就绪、实例变化或卸载时清理；超时不会把后端服务改写成 failed。
  useEffect(() => {
    if (!serviceReady || bridgeReady) return;
    const timer = window.setTimeout(() => setBridgeTimedOut(true), AI_VIDEO_BRIDGE_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [bridgeReady, instanceKey, serviceReady]);

  if (!serviceReady || !status?.url) {
    return <AiVideoServiceState status={status} requestError={error} onStart={start} />;
  }

  const badgeClass = bridgeReady
    ? "ai-video-service-badge is-ready"
    : bridgeTimedOut
      ? "ai-video-service-badge is-warning"
      : "ai-video-service-badge";
  const badgeText = bridgeReady ? "● AI 视频已连接" : bridgeTimedOut ? "● 页面桥接超时" : "● 视频服务已就绪";

  return (
    <section className="ai-video-page" aria-label="AI 视频">
      <header className="ai-video-module-header">
        <div>
          <p className="ai-video-eyebrow">AI 创作工具</p>
          <h2>AI 视频制作</h2>
          <p>从商品素材到分镜、生成与导出，全部在界野工作台内完成。</p>
        </div>
        <div className="ai-video-header-side">
          <div className="ai-video-ui-mode-toggle" role="group" aria-label="AI 视频界面模式">
            <button
              type="button"
              className={uiMode === "simple" ? "is-active" : ""}
              aria-pressed={uiMode === "simple"}
              disabled={!serviceReady || !iframeOrigin}
              onClick={() => setUiMode("simple")}
            >
              小白模式
            </button>
            <button
              type="button"
              className={uiMode === "pro" ? "is-active" : ""}
              aria-pressed={uiMode === "pro"}
              disabled={!serviceReady || !iframeOrigin}
              onClick={() => setUiMode("pro")}
            >
              导演模式
            </button>
          </div>
          <span className={badgeClass}>{badgeText}</span>
        </div>
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
        key={instanceKey ?? "clipforge-none"}
        ref={frameRef}
        className="ai-video-frame"
        title="ClipForge AI 视频"
        src={embeddedClipForgeUrl(status.url, AI_VIDEO_INITIAL_HREF)}
      />
    </section>
  );
}
