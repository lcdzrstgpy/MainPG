import { useCallback, useEffect, useRef, useState } from "react";
import {
  BACKEND_OFFLINE_EVENT,
  BACKEND_ONLINE_EVENT,
  backendHealthUrl,
  resetBackendOfflineState,
} from "../../transport/http/client";
import { hasRunningTask } from "../runtime/taskActivity";

/** 弹窗期间自动探活间隔：后端恢复后不用等用户操作，自动刷新回工作台。 */
const AUTO_RECHECK_INTERVAL_MS = 5_000;
/** 单次探活超时：防止后端半死（端口在但请求不响应）时探测一直悬着。 */
const HEALTH_CHECK_TIMEOUT_MS = 4_000;
/**
 * 弹遮罩前的确认探活超时。
 *
 * 这里**故意比自动探活宽松**：低配机器跑重任务时 CPU/磁盘被占满，后端可能十几秒
 * 才回得了一次请求，那是「忙」不是「死」。放宽到 6 秒，宁可晚一点弹，也别误弹。
 */
const CONFIRM_PROBE_TIMEOUT_MS = 6_000;
/** 第一次确认失败后隔多久再试一次（两次都失败才认）。 */
const CONFIRM_PROBE_RETRY_DELAY_MS = 2_000;
/** 第二次确认的超时（已经给过 6 秒机会了，这轮不需要那么久）。 */
const CONFIRM_PROBE_RETRY_TIMEOUT_MS = 3_000;
/** 协议拉起本程序后，等待后端就绪的预算（双击启动一般 3~10 秒）。 */
const RESTART_WAIT_BUDGET_MS = 30_000;
/** 重启等待期间的探活节奏。 */
const RESTART_POLL_INTERVAL_MS = 1_500;
/** 恢复协议：应用启动时自注册（run_workbench.ensure_url_protocol），点击「重新连接」
 *  时由浏览器交给系统执行，重新拉起被终止的主程序。 */
const MAINPG_RECOVER_URL = "mainpg://recover";

/** 「重新连接」状态机：idle → connecting →(仍失联)recovering →(等待)idle / timeout。 */
type RetryPhase = "idle" | "connecting" | "recovering" | "timeout" | "dev-manual";

const STATUS_TEXT: Record<RetryPhase, string> = {
  idle: "已开启自动检测，恢复后页面会自动刷新",
  connecting: "正在重新连接本地服务…",
  recovering: "正在重新启动本地程序——若系统弹出「打开」确认框，请点「打开」",
  timeout: "本地程序未在预期时间内恢复——请按下方方法手动重启",
  // 开发态不拉起 mainpg:// 协议：该协议注册的是「最后一次启动的程序」，本地开发
  // 时它指向已安装的 MainPG.exe（实测注册表 command 就是安装目录下那个 exe）。
  // 点一下「重新连接」会静默拉起安装版、把它绑到 8010，页面看着恢复了，实际连的
  // 是另一套数据源（%APPDATA%\MainPG），比不恢复更难排查。
  "dev-manual": "开发模式下不会自动重启本地服务——请在项目根目录运行 start-dev.bat 重启前后端",
};

/** 恢复提示条的自动消失时间（有任务在跑时不会整页刷新，用提示条代替）。 */
const RESUME_NOTICE_MS = 8_000;
/** 批量请求连环失败时的合并窗口：同一波失败只处理一次。 */
const OFFLINE_DEBOUNCE_MS = 1_000;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

/** 探测本地后端健康端点；恢复返回 true。超时按调用场景给，见上方常量。 */
async function probeHealth(timeoutMs: number = HEALTH_CHECK_TIMEOUT_MS): Promise<boolean> {
  try {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      // 探后端真实健康端点：打包版同源直连 8010；开发态由 vite 代理 /health 转发。
      const response = await fetch(backendHealthUrl(), { cache: "no-store", signal: controller.signal });
      // 必须校验返回 JSON：开发态 /health 若未被代理，vite 会回 SPA 的 index.html(200)，
      // 只认 response.ok 会把这种「假成功」当成后端已恢复。
      const contentType = response.headers.get("content-type") ?? "";
      const healthy = response.ok && contentType.includes("application/json");
      // 探活成功同样是「后端活着」的铁证，必须让 client 的失联状态机复位：
      // probeHealth 走裸 fetch，不经过 httpJson，不复位就会让状态机停在「已通知
      // 离线」，之后真掉线时不再弹提示（2026-10-16 复查发现的漏报路径）。
      if (healthy) resetBackendOfflineState();
      return healthy;
    } finally {
      window.clearTimeout(timeoutId);
    }
  } catch {
    return false;
  }
}

/**
 * 本地后端失联提示页（整屏遮罩）。
 *
 * 触发：transport/http/client 在 fetch 网络层失败（连接被拒）时广播
 * `mainpg:backend-offline`。最常见的成因是本地后端子进程被杀毒软件拦截/清除，
 * 页面还活着但所有接口都打不通——与其白屏，不如明说原因和自救办法。
 * 恢复：① 弹窗期间每 5 秒自动探活，后端恢复即整页刷新；② 任意一次成功的 HTTP
 * 响应广播 `mainpg:backend-online` 也会自动收起；③ 用户点「重新连接」= 立即探活，
 * 仍失联则借 mainpg:// 协议请系统重新拉起主程序（浏览器会先弹「打开」确认）。
 *
 * 注意：网页自身无法拉起进程，重启只能经系统协议完成；协议未注册（旧版安装）或
 * 用户在浏览器确认框中拒绝时，会退化为弹窗内的手动重启引导。
 */
export function BackendOfflineNotice() {
  const [offline, setOffline] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [phase, setPhase] = useState<RetryPhase>("idle");
  /** 「已恢复、但为保任务未整页刷新」的提示条。 */
  const [resumeNotice, setResumeNotice] = useState(false);
  /** 「后端无响应，但有任务在跑」——只出提示条，不糊整屏。 */
  const [degraded, setDegraded] = useState(false);
  const timerRef = useRef<number | null>(null);
  const offlineRef = useRef(false);
  const recoveringRef = useRef(false);
  /** 每次「恢复在线」都自增：探活是异步的，回来时要确认期间没被别人翻盘。 */
  const onlineEpochRef = useRef(0);

  useEffect(() => {
    offlineRef.current = offline;
  }, [offline]);

  useEffect(() => {
    const showOffline = () => {
      setPhase("idle");
      // 重新掉线时收起上一次的「已恢复」提示条，避免两条浮层同时在场。
      setResumeNotice(false);
      setDegraded(false);
      setOffline(true);
    };
    /**
     * 弹遮罩前的最后一道闸：主动探活。
     *
     * 状态机的「6 秒内没有成功」只等于「没有反证」，不等于「后端真的死了」——
     * 产品处理等通道此前不复位状态机，一次瞬时抖动就能凑满窗口（2026-10-19）。
     * 这里实测一次 /health，失败才弹；成功则顺手把状态机复位（probeHealth 内部做）。
     */
    const confirmOffline = async () => {
      const epoch = onlineEpochRef.current;
      // 第一次确认：给足 6 秒。低配机器跑重任务时后端可能只是「忙」，不是死。
      if (await probeHealth(CONFIRM_PROBE_TIMEOUT_MS)) return;
      // 探活期间页面恢复了在线（别的请求成功）→ 不再弹。
      if (onlineEpochRef.current !== epoch) return;
      // 第二次确认：隔 2 秒再来，仍然没响应才认账。
      await delay(CONFIRM_PROBE_RETRY_DELAY_MS);
      if (onlineEpochRef.current !== epoch) return;
      if (await probeHealth(CONFIRM_PROBE_RETRY_TIMEOUT_MS)) return;
      if (onlineEpochRef.current !== epoch) return;
      // 有任务在跑时绝不整屏糊住界面：用户正盯着进度，遮罩会让他看不到也动不了。
      // 而且「忙到答不上话」和「真死了」此刻分不清——降级成非模态提示条，任务照跑、
      // 进度照刷，恢复后自动收起。
      const tasksBusy = hasRunningTask();
      if (tasksBusy) {
        setDegraded(true);
        return;
      }
      showOffline();
    };
    const goOffline = () => {
      // 防抖：批量请求连环失败只处理一次。
      if (timerRef.current !== null) return;
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        void confirmOffline();
      }, OFFLINE_DEBOUNCE_MS);
    };
    const goOnline = () => {
      onlineEpochRef.current += 1;
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      // 遮罩确实弹过、且有任务在跑（不能整页刷新）→ 用提示条明确告知「已恢复」。
      // 否则遮罩凭空消失，用户以为点了「重新连接」没反应（2026-10-16 反馈）。
      if (offlineRef.current && hasRunningTask()) setResumeNotice(true);
      setPhase("idle");
      setOffline(false);
      setDegraded(false);
      setRetrying(false);
    };
    window.addEventListener(BACKEND_OFFLINE_EVENT, goOffline);
    window.addEventListener(BACKEND_ONLINE_EVENT, goOnline);
    return () => {
      window.removeEventListener(BACKEND_OFFLINE_EVENT, goOffline);
      window.removeEventListener(BACKEND_ONLINE_EVENT, goOnline);
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, []);

  const reloadWhenRecovered = useCallback(() => {
    // 有任务在跑时绝不整页刷新：应用内标签页是内存态，刷新会把处理页标签一起清掉，
    // 处理页卸载 → 心跳断 → 后端 90 秒后把任务自动暂停（2026-10-15 线上复现）。
    // 这里只收起提示，让页面自己继续轮询恢复。
    if (hasRunningTask()) {
      setOffline(false);
      setDegraded(false);
      setResumeNotice(true);
      return;
    }
    // 弹窗已被其它成功请求收起时不再强行刷新。
    if (recoveringRef.current || !offlineRef.current) return;
    recoveringRef.current = true;
    setOffline(false);
    window.location.reload();
  }, []);

  // 弹窗/降级提示期间自动探活：后端恢复后用户不操作也能自动刷新回工作台。
  useEffect(() => {
    if (!offline && !degraded) return;
    const timer = window.setInterval(() => {
      void probeHealth().then((healthy) => {
        if (healthy) reloadWhenRecovered();
      });
    }, AUTO_RECHECK_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [offline, degraded, reloadWhenRecovered]);

  // 页面被浏览器「捡回来」时自愈：
  // ① 从 bfcache 恢复（pageshow.persisted）→ 整页 DOM 与模块状态都是旧的，
  //    遮罩会永远停在那儿，自动探活的定时器也可能根本没在跑；
  // ② 标签页在后台被节流时定时器会被拉长到分钟级，切回前台要立刻探一次，
  //    否则用户看到的还是「掉线」。
  // 这两条正是「叉掉浏览器再点启动，还是那个掉线弹窗」的现场（2026-10-19 反馈）。
  useEffect(() => {
    const onPageShow = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      if (!offlineRef.current) {
        // 没弹遮罩：先把状态机复位，让后续请求重新判定即可。
        resetBackendOfflineState();
        return;
      }
      void probeHealth().then((healthy) => {
        if (healthy) reloadWhenRecovered();
      });
    };
    const onVisible = () => {
      if (document.visibilityState !== "visible" || !offlineRef.current) return;
      void probeHealth().then((healthy) => {
        if (healthy) reloadWhenRecovered();
      });
    };
    window.addEventListener("pageshow", onPageShow);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("pageshow", onPageShow);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [reloadWhenRecovered]);

  // 恢复提示条自动消失，不长期占位。
  useEffect(() => {
    if (!resumeNotice) return;
    const timer = window.setTimeout(() => setResumeNotice(false), RESUME_NOTICE_MS);
    return () => window.clearTimeout(timer);
  }, [resumeNotice]);

  if (!offline && !resumeNotice && !degraded) return null;

  const retry = async () => {
    if (retrying) return;
    setRetrying(true);
    setPhase("connecting");
    try {
      if (await probeHealth()) {
        reloadWhenRecovered();
        return;
      }
      if (import.meta.env.DEV) {
        // 开发态绝不拉起 mainpg:// 协议：该协议注册的是「最后一次启动的程序」，本地
        // 开发时指向已安装的 MainPG.exe，拉起来会静默把它绑到 8010 并换掉数据源
        // （%APPDATA%\MainPG），页面看着恢复了、实际连的是另一套数据，更难排查。
        setPhase("dev-manual");
        return;
      }
      // 仍失联：借系统协议重新拉起主程序。只在用户点击时触发，自动探活不触发，
      // 避免主程序被安全软件反复终止时形成重启循环。
      setPhase("recovering");
      window.location.href = MAINPG_RECOVER_URL;
      const deadline = Date.now() + RESTART_WAIT_BUDGET_MS;
      while (Date.now() < deadline) {
        await delay(RESTART_POLL_INTERVAL_MS);
        if (recoveringRef.current) return;
        if (await probeHealth()) {
          reloadWhenRecovered();
          return;
        }
      }
      setPhase("timeout");
    } finally {
      setRetrying(false);
    }
  };

  const buttonLabel = retrying
    ? (phase === "recovering" ? "正在重新启动…" : "正在重新连接…")
    : "重新连接";

  return (
    <>
      {degraded && !offline && (
        <div className="backend-resume-toast backend-resume-toast--warn" role="status">
          <span className="backend-resume-toast-text">
            本地服务暂时没有响应，正在自动重连。任务还在跑，进度可能延迟更新。
          </span>
          <button
            type="button"
            className="backend-resume-toast-close"
            onClick={() => {
              void probeHealth(HEALTH_CHECK_TIMEOUT_MS).then((healthy) => {
                if (healthy) reloadWhenRecovered();
              });
            }}
          >
            立即重试
          </button>
        </div>
      )}
      {resumeNotice && (
        <div className="backend-resume-toast" role="status">
          <span className="backend-resume-toast-text">
            本地服务已恢复。因为有任务正在运行，没有自动刷新页面，以免打断任务。
          </span>
          <button
            type="button"
            className="backend-resume-toast-close"
            onClick={() => setResumeNotice(false)}
          >
            知道了
          </button>
        </div>
      )}
      {offline && (
    <div className="backend-offline-mask" role="alertdialog" aria-modal="true" aria-labelledby="backend-offline-title">
      <div className="backend-offline-card">
        <svg className="backend-offline-illo" viewBox="0 0 200 118" aria-hidden="true">
          <rect className="illo-body" x="28" y="30" width="56" height="62" rx="18" />
          <circle className="illo-eye" cx="46" cy="58" r="4.2" />
          <circle className="illo-eye" cx="66" cy="58" r="4.2" />
          <circle className="illo-line" cx="56" cy="76" r="4.5" />
          <ellipse className="illo-blush" cx="38" cy="70" rx="4.6" ry="2.8" opacity=".6" />
          <ellipse className="illo-blush" cx="74" cy="70" rx="4.6" ry="2.8" opacity=".6" />
          <g className="illo-plug">
            <path className="illo-cable-fill" d="M158,30 c2.6,3.4 2.6,6.4 0,7.8 c-2.6,-1.4 -2.6,-4.4 0,-7.8 z" opacity=".85" />
            <line className="illo-cable" x1="97" y1="56" x2="112" y2="56" />
            <line className="illo-cable" x1="97" y1="72" x2="112" y2="72" />
            <rect className="illo-body" x="112" y="44" width="42" height="40" rx="14" />
            <line className="illo-line" x1="122" y1="63" x2="132" y2="63" />
            <line className="illo-line" x1="135" y1="63" x2="145" y2="63" />
            <path className="illo-line" d="M127,75 q5,-4.5 10,0" />
            <path className="illo-cable" d="M154,66 C168,68 162,86 180,88" />
            <circle className="illo-cable-fill" cx="184" cy="90" r="2.6" />
          </g>
          <path className="illo-spark" d="M92,42.5 l2.1,5.2 5.2,2.3 -5.2,2.3 -2.1,5.2 -2.1,-5.2 -5.2,-2.3 5.2,-2.3 z" />
          <circle className="illo-spark" cx="104" cy="57" r="1.8" opacity=".75" />
          <circle className="illo-spark" cx="86" cy="58" r="1.4" opacity=".5" />
        </svg>
        <h2 id="backend-offline-title" className="backend-offline-title">本地服务掉线了</h2>
        <p className="backend-offline-sub">别担心，账号和数据都安全，只是本地服务断了连接。</p>
        <button type="button" className="backend-offline-retry" disabled={retrying} onClick={retry} autoFocus>
          {retrying
            ? <span className="backend-offline-spin" aria-hidden="true" />
            : (
              <svg className="backend-offline-retry-icon" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M21 12a9 9 0 1 1-2.6-6.3" />
                <path d="M21 3v6h-6" />
              </svg>
            )}
          <span>{buttonLabel}</span>
        </button>
        <p className="backend-offline-status">
          <span className="backend-offline-dot" aria-hidden="true" />
          <span>{STATUS_TEXT[phase]}</span>
        </p>
        <button
          type="button"
          className="backend-offline-help-toggle"
          aria-expanded={helpOpen}
          onClick={() => setHelpOpen((open) => !open)}
        >
          还是连不上？查看解决方法
          <svg className="backend-offline-chev" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M6 9l6 6 6-6" />
          </svg>
        </button>
        {helpOpen && (
          <div className="backend-offline-help">
            <div className="backend-offline-step">
              <span className="backend-offline-num" aria-hidden="true">1</span>
              <span>完全退出 MainPG 后再重新打开即可</span>
            </div>
            <div className="backend-offline-step">
              <span className="backend-offline-num" aria-hidden="true">2</span>
              <span>打开杀毒软件（电脑管家 / 360 / 火绒），把 MainPG 安装目录加入信任区，重启主程序</span>
            </div>
          </div>
        )}
      </div>
    </div>
      )}
    </>
  );
}
