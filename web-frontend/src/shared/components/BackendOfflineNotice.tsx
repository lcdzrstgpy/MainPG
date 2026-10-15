import { useCallback, useEffect, useRef, useState } from "react";
import {
  BACKEND_OFFLINE_EVENT,
  BACKEND_ONLINE_EVENT,
  backendHealthUrl,
} from "../../transport/http/client";
import { hasRunningTask } from "../runtime/taskActivity";

/** 弹窗期间自动探活间隔：后端恢复后不用等用户操作，自动刷新回工作台。 */
const AUTO_RECHECK_INTERVAL_MS = 5_000;
/** 单次探活超时：防止后端半死（端口在但请求不响应）时探测一直悬着。 */
const HEALTH_CHECK_TIMEOUT_MS = 4_000;
/** 协议拉起本程序后，等待后端就绪的预算（双击启动一般 3~10 秒）。 */
const RESTART_WAIT_BUDGET_MS = 30_000;
/** 重启等待期间的探活节奏。 */
const RESTART_POLL_INTERVAL_MS = 1_500;
/** 恢复协议：应用启动时自注册（run_workbench.ensure_url_protocol），点击「重新连接」
 *  时由浏览器交给系统执行，重新拉起被终止的主程序。 */
const MAINPG_RECOVER_URL = "mainpg://recover";

/** 「重新连接」状态机：idle → connecting →(仍失联)recovering →(等待)idle / timeout。 */
type RetryPhase = "idle" | "connecting" | "recovering" | "timeout";

const STATUS_TEXT: Record<RetryPhase, string> = {
  idle: "已开启自动检测，恢复后页面会自动刷新",
  connecting: "正在重新连接本地服务…",
  recovering: "正在重新启动本地程序——若系统弹出「打开」确认框，请点「打开」",
  timeout: "本地程序未在预期时间内恢复——请按下方方法手动重启",
};

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

/** 探测本地后端健康端点；恢复返回 true。 */
async function probeHealth(): Promise<boolean> {
  try {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), HEALTH_CHECK_TIMEOUT_MS);
    try {
      // 探后端真实健康端点：打包版同源直连 8010；开发态由 vite 代理 /health 转发。
      const response = await fetch(backendHealthUrl(), { cache: "no-store", signal: controller.signal });
      // 必须校验返回 JSON：开发态 /health 若未被代理，vite 会回 SPA 的 index.html(200)，
      // 只认 response.ok 会把这种「假成功」当成后端已恢复。
      const contentType = response.headers.get("content-type") ?? "";
      return response.ok && contentType.includes("application/json");
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
  const timerRef = useRef<number | null>(null);
  const offlineRef = useRef(false);
  const recoveringRef = useRef(false);

  useEffect(() => {
    offlineRef.current = offline;
  }, [offline]);

  useEffect(() => {
    const goOffline = () => {
      // 防抖 1s：批量请求连环失败只弹一次。
      if (timerRef.current !== null) return;
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        setPhase("idle");
        setOffline(true);
      }, 1000);
    };
    const goOnline = () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      setPhase("idle");
      setOffline(false);
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
      return;
    }
    // 弹窗已被其它成功请求收起时不再强行刷新。
    if (recoveringRef.current || !offlineRef.current) return;
    recoveringRef.current = true;
    setOffline(false);
    window.location.reload();
  }, []);

  // 弹窗期间自动探活：后端恢复后用户不操作也能自动刷新回工作台。
  useEffect(() => {
    if (!offline) return;
    const timer = window.setInterval(() => {
      void probeHealth().then((healthy) => {
        if (healthy) reloadWhenRecovered();
      });
    }, AUTO_RECHECK_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [offline, reloadWhenRecovered]);

  if (!offline) return null;

  const retry = async () => {
    if (retrying) return;
    setRetrying(true);
    setPhase("connecting");
    try {
      if (await probeHealth()) {
        reloadWhenRecovered();
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
  );
}
