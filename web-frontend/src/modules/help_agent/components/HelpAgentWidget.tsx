import { type CSSProperties, useCallback, useEffect, useRef, useState } from "react";

import { useTheme, type ThemeId } from "../../../shared/hooks/useTheme";
import { getAuthToken } from "../../../transport/http/client";
import { showToast } from "../../../shared/components/toastStore";
import { workspacePageModules, type WorkspaceModuleId } from "../../../app/navigation/modules";
import { helpAgentApi, newSessionId } from "../api/helpAgentApi";
import {
  clampBallPosition,
  isDrag,
  isValidStoredPosition,
  type ChatMessage,
} from "../data/chatMessage";
import { useBallSize, type BallSizeId } from "../../../shared/hooks/useBallSize";
import "../styles/helpAgent.css";

const DRAG_THRESHOLD_PX = 4;
const BALL_SIZE = 56;
/** 按偏好设置取球体直径（偏好设置 → 悬浮球大小）；中档 88 为工作台既有大小。 */
const PREF_BALL_SIZES: Record<BallSizeId, number> = { sm: 68, md: 88, lg: 110 };
const PANEL_WIDTH = 380;
const PANEL_HEIGHT = 520;
const PANEL_MARGIN = 16;
const POSITION_STORAGE_KEY = "help_agent_ball_pos";
/** 面板收起动效时长；到点才真正卸载，避免关闭时直接消失。与 helpAgent.css 的 out 动画保持一致。 */
const PANEL_EXIT_MS = 180;

/* -- 球旁边的引导气泡 ----------------------------------------------------
 * 只在工作台实例、登录后出现：登录页那个球是置灰的，弹气泡会点了没反应。
 * 展示节奏是随机的（见 HINT_FIRST_DELAY 与 HINT_GAP_RANGE），气泡里带「关闭气泡」，
 * 用户点过之后就永久不再出现（只写 localStorage 的一个开关，不做账号同步）。 */
const HINT_OFF_STORAGE_KEY = "help_agent_hint_off";
/** 首次出现的延迟区间（毫秒）：让页面先加载完，别一进来就弹。 */
const HINT_FIRST_DELAY: readonly [number, number] = [4_000, 12_000];
/** 每次自动消失后，到下一次出现的随机间隔：2~6 分钟，避免像定时骚扰。 */
const HINT_GAP_RANGE: readonly [number, number] = [120_000, 360_000];
/** 每次气泡停留时长。 */
const HINT_VISIBLE_MS = 15_000;
const HINT_WIDTH = 208;

/** 气泡文案池：随机取一句，且不会连续两次相同。 */
const HINT_PHRASES: readonly string[] = [
  "哪里不会用，我告诉你～",
  "工作台我太熟啦，点点我问我呀！",
  "POD、AI处理、积分规则，随便问～",
  "卡在哪一步了？问我最快",
  "上新遇到问题？我在这儿",
  "不会导出店小秘？点我问问",
  "积分到底怎么扣？一问便知",
  "新手先问我，少走弯路～",
];

function randomBetween(range: readonly [number, number]): number {
  return range[0] + Math.random() * (range[1] - range[0]);
}

function pickHintPhrase(previous: string): string {
  const pool = HINT_PHRASES.filter((phrase) => phrase !== previous);
  return pool[Math.floor(Math.random() * pool.length)] ?? HINT_PHRASES[0];
}

/** 各主题专属 Q 版吉祥物（球面图）。 */
const THEME_MASCOT: Record<ThemeId, string> = {
  classic: "/theme/mascots/01-classic.png",
  sunset: "/theme/mascots/02-warm-orange.png",
  violet: "/theme/mascots/03-sakura-purple.png",
  dessert: "/theme/mascots/04-caramel.png",
  diamond: "/theme/mascots/05-diamond.png",
  quirky: "/theme/mascots/06-sticker.png",
  chinese: "/theme/mascots/07-ink.png",
  peach: "/theme/mascots/08-peach.png",
  alpine: "/theme/mascots/10-alpine.png",
};

/**
 * 空态推荐问题按「模块 × 用法 / 收费」两条主线生成。
 *
 * 模块中文名直接取导航配置（``workspacePageModules``），改模块名不用回来改这里；
 * 只保留两个主力模块，四个问题刚好铺满一屏，多了一屏放不下反而没人点。
 */
const SUGGESTED_MODULE_IDS: WorkspaceModuleId[] = ["product_processing", "pod_customization"];

function moduleLabel(id: WorkspaceModuleId): string {
  return workspacePageModules.find((module) => module.id === id)?.label ?? id;
}

const SUGGESTED_QUESTIONS = [
  ...SUGGESTED_MODULE_IDS.map((id) => `${moduleLabel(id)}模块怎么使用？`),
  ...SUGGESTED_MODULE_IDS.map((id) => `${moduleLabel(id)}模块积分怎么收费？`),
];

type BallPosition = { x: number; y: number };

type HelpAgentWidgetProps = {
  /**
   * 球体大小是否跟随「偏好设置 → 悬浮球大小」（只在工作台里传 true）。
   *
   * 登录页那个实例没有偏好入口，固定用 56px 小球；工作台里按三档尺寸跟随主题吉祥物。
   */
  sizeFromPreference?: boolean;
};

function readStoredPosition(): BallPosition | null {
  try {
    const raw = window.localStorage.getItem(POSITION_STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return isValidStoredPosition(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/** 默认位置：右下角，和「返回顶部」按钮错开一点。 */
function defaultPosition(ballSize: number): BallPosition {
  return {
    x: window.innerWidth - ballSize - 24,
    y: window.innerHeight - ballSize - 96,
  };
}

function makeId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

/** 单轴夹取：把 value 限制在 [min(下限), 上限] 之间；上限小于下限时取下限。 */
function clampAxis(value: number, max: number, min: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

export function HelpAgentWidget({ sizeFromPreference = false }: HelpAgentWidgetProps = {}) {
  const { theme } = useTheme();
  const { size: ballSizePref } = useBallSize();
  // 答疑在服务端，必须登录：未登录时球置灰，点击提示先登录，不发请求。
  const authenticated = Boolean(getAuthToken());
  const [open, setOpen] = useState(false);
  /** 正在播放收起动效：面板仍挂载，但已经缩小淡出，结束后才 ``setOpen(false)``。 */
  const [closing, setClosing] = useState(false);
  /** 刚打开面板时让吉祥物点一下头（和面板展开动效呼应），500ms 后摘掉类名恢复待机动画。 */
  const [greeting, setGreeting] = useState(false);
  /** 球旁边的引导气泡：是否可见、当前文案、用户是否已永久关闭。 */
  const [hintVisible, setHintVisible] = useState(false);
  const [hintPhrase, setHintPhrase] = useState(HINT_PHRASES[0]);
  const [hintDisabled, setHintDisabled] = useState(() => {
    try {
      return window.localStorage.getItem(HINT_OFF_STORAGE_KEY) === "1";
    } catch {
      return false;
    }
  });
  // 大小偏好只作用于工作台实例（登录页那个球没有偏好入口，保持 56px）。
  const ballSize = sizeFromPreference ? PREF_BALL_SIZES[ballSizePref] : BALL_SIZE;
  // 初始化就要夹取：localStorage 里可能是大窗口下拖拽存下的坐标，直接沿用会停在屏幕外，
  // 而 resize 事件只在窗口尺寸变化时才触发，球就再也回不来了。
  const [position, setPosition] = useState<BallPosition>(() => clampBallPosition(
    readStoredPosition() ?? defaultPosition(ballSize),
    { width: window.innerWidth, height: window.innerHeight },
    ballSize,
  ));
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  /** 正在流式输出的助手消息 id；非空时禁止再发。 */
  const [streamingId, setStreamingId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const dragState = useRef<{ startX: number; startY: number; originX: number; originY: number; moved: boolean } | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  /** 同步的在飞标记：state 更新要等下一次渲染，连按两次回车会穿过 ``streamingId`` 判断。 */
  const busyRef = useRef(false);
  /** 中文输入法组词中：这期间的回车是「选词」，不是发送。 */
  const composingRef = useRef(false);
  /** 服务端按 session_id 保留最近 3 轮上下文，所以整个面板生命周期共用一个。 */
  const sessionIdRef = useRef(newSessionId());
  const closeTimerRef = useRef<number | null>(null);

  /**
   * 收起面板：先播放「缩回球里」的动画，``PANEL_EXIT_MS`` 之后再卸载。
   *
   * 重复调用直接忽略（动画期间再点球/再按 Esc 不会叠加定时器）。
   * 在飞请求立刻中止——动画只是视觉残留，没必要继续占着服务端连接。
   */
  const requestClose = useCallback(() => {
    if (closeTimerRef.current != null) return;
    abortRef.current?.abort();
    setClosing(true);
    closeTimerRef.current = window.setTimeout(() => {
      closeTimerRef.current = null;
      setClosing(false);
      setOpen(false);
    }, PANEL_EXIT_MS);
  }, []);

  /** 收起当前这条气泡；下一次出现的时机由下面的随机调度继续排。 */
  const hideHint = useCallback(() => {
    setHintVisible(false);
  }, []);

  /** 「关闭气泡」：永久停用，记在 localStorage 里，换页面/重启都不再弹。 */
  const disableHint = useCallback(() => {
    setHintVisible(false);
    setHintDisabled(true);
    try {
      window.localStorage.setItem(HINT_OFF_STORAGE_KEY, "1");
    } catch {
      /* localStorage 不可用时只在本次会话生效 */
    }
  }, []);

  // 不定时随机展示：首次 4~12 秒后出现，之后每 2~6 分钟随机一次；
  // 每条停留 15 秒自动收起。面板打开期间不弹（用户已经在用了）。
  useEffect(() => {
    if (!authenticated || !sizeFromPreference || open || hintDisabled) return;
    let stopped = false;
    let showTimer = 0;
    let hideTimer = 0;

    const schedule = (delay: number) => {
      showTimer = window.setTimeout(() => {
        if (stopped) return;
        setHintPhrase((previous) => pickHintPhrase(previous));
        setHintVisible(true);
        hideTimer = window.setTimeout(() => {
          if (stopped) return;
          setHintVisible(false);
          schedule(randomBetween(HINT_GAP_RANGE));
        }, HINT_VISIBLE_MS);
      }, delay);
    };

    schedule(randomBetween(HINT_FIRST_DELAY));
    return () => {
      stopped = true;
      window.clearTimeout(showTimer);
      window.clearTimeout(hideTimer);
    };
  }, [authenticated, hintDisabled, open, sizeFromPreference]);

  // 窗口缩放、或偏好设置里改了球体大小：把悬浮球拉回可视区，
  // 避免缩小窗口 / 换成大球后球有一部分留在屏幕外找不回来。
  // 用户从没拖动过球（localStorage 无坐标）时直接按新尺寸重算右下角默认位置，
  // 否则换成大球后会残留"按旧尺寸算出来的边距"。
  useEffect(() => {
    const keepInView = () => {
      const viewport = { width: window.innerWidth, height: window.innerHeight };
      setPosition((current) =>
        readStoredPosition() ? clampBallPosition(current, viewport, ballSize) : defaultPosition(ballSize),
      );
    };
    keepInView();
    window.addEventListener("resize", keepInView);
    return () => window.removeEventListener("resize", keepInView);
  }, [ballSize]);

  // 打开面板时吉祥物点一下头；播完就摘掉类名，待机动画（呼吸 / 浮动）继续跑。
  useEffect(() => {
    if (!open) return;
    setGreeting(true);
    const timer = window.setTimeout(() => setGreeting(false), 500);
    return () => window.clearTimeout(timer);
  }, [open]);

  // 新消息到达时滚到底部。
  useEffect(() => {
    const body = bodyRef.current;
    if (body) body.scrollTop = body.scrollHeight;
  }, [messages, open]);

  // 组件卸载时中止在飞的流式请求并清掉收起定时器，避免连接挂着 / 卸载后 setState。
  useEffect(() => () => {
    if (closeTimerRef.current != null) window.clearTimeout(closeTimerRef.current);
    abortRef.current?.abort();
  }, []);

  // 打开面板时聚焦输入框，Esc 收起（走同一套收起动效）。
  useEffect(() => {
    if (!open) return;
    if (authenticated) inputRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") requestClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, authenticated, requestClose]);

  const persistPosition = useCallback((next: BallPosition) => {
    try {
      window.localStorage.setItem(POSITION_STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* localStorage 不可用时静默忽略，位置只在本次会话生效 */
    }
  }, []);

  // -- 悬浮球拖拽 --------------------------------------------------------

  const onPointerDown = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0) return;
    dragState.current = {
      startX: event.clientX,
      startY: event.clientY,
      originX: position.x,
      originY: position.y,
      moved: false,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }, [position.x, position.y]);

  const onPointerMove = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    const drag = dragState.current;
    if (!drag) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    if (!drag.moved && !isDrag({ dx, dy }, DRAG_THRESHOLD_PX)) return;
    drag.moved = true;
    setPosition(clampBallPosition(
      { x: drag.originX + dx, y: drag.originY + dy },
      { width: window.innerWidth, height: window.innerHeight },
      ballSize,
    ));
  }, [ballSize]);

  const onPointerUp = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    const drag = dragState.current;
    dragState.current = null;
    event.currentTarget.releasePointerCapture(event.pointerId);
    if (!drag) return;
    if (drag.moved) {
      // 拖过就不算点击，避免拖完手一松误开面板。
      persistPosition(position);
      return;
    }
    if (!authenticated) {
      showToast("请先登录后再使用私人助手");
      return;
    }
    if (open) requestClose();
    else {
      setOpen(true);
      hideHint();
    }
  }, [authenticated, hideHint, open, persistPosition, position, requestClose]);

  // -- 问答 --------------------------------------------------------------

  const ask = useCallback(async (raw: string) => {
    const question = raw.trim();
    if (!question || streamingId || busyRef.current || !authenticated) return;
    busyRef.current = true;

    const answerId = makeId("a");
    setMessages((current) => [
      ...current,
      { id: makeId("u"), role: "user", text: question },
      { id: answerId, role: "assistant", text: "", streaming: true },
    ]);
    setDraft("");
    setError("");
    setStreamingId(answerId);

    const controller = new AbortController();
    abortRef.current = controller;
    const patch = (next: (message: ChatMessage) => ChatMessage) => {
      setMessages((current) => current.map((message) => (message.id === answerId ? next(message) : message)));
    };

    try {
      await helpAgentApi.chatStream({
        sessionId: sessionIdRef.current,
        message: question,
        signal: controller.signal,
        onMeta: (meta) => patch((message) => ({ ...message, requestId: meta.request_id })),
        onDelta: (text) => patch((message) => ({ ...message, text: message.text + text })),
        onCitation: (citation) =>
          patch((message) => ({ ...message, citations: [...(message.citations ?? []), citation] })),
      });
      patch((message) => ({ ...message, streaming: false }));
    } catch (cause) {
      // 用户主动关面板/中止：保留已收到的半截答案，不当成错误提示。
      if (cause instanceof DOMException && cause.name === "AbortError") {
        patch((message) => ({ ...message, streaming: false }));
        return;
      }
      // 没收到任何正文的空回答不留气泡，有内容则保留并停止闪烁。
      setMessages((current) => current.filter((message) => message.id !== answerId || message.text.trim().length > 0));
      setError(cause instanceof Error ? cause.message : "答疑服务暂时不可用，请稍后再试");
      // 失败时把问题放回输入框，用户手动再点一次发送（服务端要求不自动重试）。
      setDraft((current) => current || question);
    } finally {
      busyRef.current = false;
      setStreamingId(null);
      abortRef.current = null;
    }
  }, [authenticated, streamingId]);

  const handleSubmit = useCallback(() => {
    void ask(draft);
  }, [ask, draft]);

  const rateAnswer = useCallback(async (message: ChatMessage, helpful: boolean) => {
    if (!message.requestId || message.feedback) return;
    const mark: "up" | "down" = helpful ? "up" : "down";
    setMessages((current) => current.map((item) => (item.id === message.id ? { ...item, feedback: mark } : item)));
    try {
      await helpAgentApi.feedback(message.requestId, helpful);
    } catch (cause) {
      // 反馈失败不能把评价留在界面上假装成功，恢复可点并提示一次。
      setMessages((current) =>
        current.map((item) => (item.id === message.id ? { ...item, feedback: undefined } : item)),
      );
      showToast(cause instanceof Error ? cause.message : "反馈提交失败，请稍后再试", "error");
    }
  }, []);

  // -- 渲染 --------------------------------------------------------------

  // 面板尽量贴在悬浮球旁边；靠边时夹回可视区，避免超出屏幕。
  const panelLeft = clampAxis(position.x + ballSize - PANEL_WIDTH, window.innerWidth - PANEL_WIDTH - PANEL_MARGIN, PANEL_MARGIN);
  const panelTop = clampAxis(position.y - PANEL_HEIGHT - 12, window.innerHeight - PANEL_HEIGHT - PANEL_MARGIN, PANEL_MARGIN);
  const panelStyle: CSSProperties = {
    left: panelLeft,
    top: panelTop,
    // 展开动效的缩放原点放在球心：面板看起来是从球里长出来的，而不是凭空弹出。
    // 球通常贴在面板右下角之外，所以这个点允许落在面板边界外。
    transformOrigin: `${position.x + ballSize / 2 - panelLeft}px ${position.y + ballSize / 2 - panelTop}px`,
  };

  // 引导气泡：优先贴在球上方，顶部放不下就翻到球下方；右边缘与球右边缘对齐。
  // 上方用 `bottom` 定位，气泡高度随文案换行变化也始终与球保持 12px 间距。
  // 110px 是按「两行文案 + 关闭气泡小字」估的最大高度，留出余量避免贴顶被裁。
  const hintAbove = position.y >= 110;
  const hintStyle: CSSProperties = {
    left: clampAxis(position.x + ballSize - HINT_WIDTH, window.innerWidth - HINT_WIDTH - 8, 8),
    ...(hintAbove
      ? { bottom: window.innerHeight - position.y + 12 }
      : { top: position.y + ballSize + 12 }),
  };

  // 球面固定显示当前主题的 Q 版吉祥物（不再显示积分，也不做翻转）。
  const mascot = THEME_MASCOT[theme] ?? THEME_MASCOT.classic;
  const pending = streamingId !== null;

  return (
    <>
      {open && (
        <section
          className={`help-agent-panel${closing ? " is-closing" : ""}`}
          style={panelStyle}
          role="dialog"
          aria-modal="false"
          aria-label="私人助手"
        >
          <header className="help-agent-panel-head">
            <div>
              <h2>私人助手</h2>
            </div>
            <button type="button" className="help-agent-close" onClick={requestClose} aria-label="收起答疑">×</button>
          </header>

          <div className="help-agent-body" ref={bodyRef}>
            {!authenticated ? (
              <div className="help-agent-notice">
                <p>私人助手需要登录后使用。</p>
                <p>登录后可以问我系统操作、业务流程和积分规则。</p>
              </div>
            ) : (
              <>
                {messages.length === 0 && !pending && (
                  <div className="help-agent-empty">
                    <p>您也有以下困扰？</p>
                    <div className="help-agent-samples">
                      {SUGGESTED_QUESTIONS.map((sample) => (
                        <button
                          key={sample}
                          type="button"
                          className="help-agent-sample"
                          onClick={() => void ask(sample)}
                        >
                          {sample}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {messages.map((message) => (
                  <div key={message.id} className={`help-agent-msg is-${message.role}`}>
                    <div className="help-agent-bubble">
                      {message.streaming && !message.text ? (
                        <div className="help-agent-typing" aria-label="正在回答"><span /><span /><span /></div>
                      ) : (
                        <p className="help-agent-text">
                          {message.text}
                          {message.streaming && <span className="help-agent-caret" aria-hidden="true" />}
                        </p>
                      )}

                      {message.citations && message.citations.length > 0 && (
                        <ul className="help-agent-citations">
                          {message.citations.map((citation, index) => (
                            <li key={`${citation.url}-${index}`}>
                              {/* 引用是飞书文档，必须用系统浏览器打开：应用内嵌会因缺登录态白屏。 */}
                              <button
                                type="button"
                                onClick={() => window.open(citation.url, "_blank", "noopener,noreferrer")}
                              >
                                {citation.title}
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}

                      {message.role === "assistant" && message.requestId && !message.streaming && (
                        <div className="help-agent-rate">
                          <span>这个回答有用吗？</span>
                          <button
                            type="button"
                            className={message.feedback === "up" ? "is-active" : ""}
                            onClick={() => void rateAnswer(message, true)}
                            disabled={Boolean(message.feedback)}
                          >
                            有用
                          </button>
                          <button
                            type="button"
                            className={message.feedback === "down" ? "is-active" : ""}
                            onClick={() => void rateAnswer(message, false)}
                            disabled={Boolean(message.feedback)}
                          >
                            没用
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </>
            )}
          </div>

          <footer className="help-agent-foot">
            {error && <p className="help-agent-error">{error}</p>}
            <div className="help-agent-input-row">
              <textarea
                ref={inputRef}
                className="help-agent-input"
                rows={1}
                maxLength={1000}
                placeholder={authenticated ? "告诉我你的困扰～" : "请先登录后再提问"}
                value={draft}
                disabled={!authenticated}
                onChange={(event) => setDraft(event.target.value)}
                onCompositionStart={() => { composingRef.current = true; }}
                onCompositionEnd={() => { composingRef.current = false; }}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" || event.shiftKey) return;
                  // 中文输入法用回车选词时也会先发 keydown：此时回车属于「确认候选」，
                  // 不能当发送，否则打一个字就被直接发出去。
                  if (composingRef.current || event.nativeEvent.isComposing) return;
                  event.preventDefault();
                  handleSubmit();
                }}
              />
              <button
                type="button"
                className="help-agent-send"
                onClick={handleSubmit}
                disabled={!authenticated || pending || !draft.trim()}
              >
                发送
              </button>
            </div>
            {/* 登录后不再挂底部说明行；未登录时保留一句「登录后即可提问」的引导。 */}
            {!authenticated && <p className="help-agent-hint">登录后即可提问</p>}
          </footer>
        </section>
      )}

      {/* 球旁边的引导气泡：点文案直接打开答疑面板；「关闭气泡」永久停用。 */}
      {hintVisible && !open && (
        <div className={`help-agent-hint-bubble${hintAbove ? "" : " is-below"}`} style={hintStyle}>
          <button
            type="button"
            className="help-agent-hint-text"
            onClick={() => {
              hideHint();
              setOpen(true);
            }}
          >
            {hintPhrase}
          </button>
          <button type="button" className="help-agent-hint-off" onClick={disableHint}>
            关闭气泡
          </button>
        </div>
      )}

      <button
        type="button"
        className={`help-agent-ball is-mascot${sizeFromPreference ? " is-sized" : ""}${authenticated ? "" : " is-disabled"}${greeting ? " is-greeting" : ""}`}
        style={{ left: position.x, top: position.y }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        aria-label={authenticated ? (open ? "收起私人助手" : "打开私人助手") : "登录后才能使用私人助手"}
        title={!authenticated ? "请先登录后再使用私人助手" : "私人助手（可拖动）"}
      >
        <span className="help-agent-ball-face">
          <img src={mascot} alt="答疑助手" draggable={false} />
        </span>
      </button>
    </>
  );
}
