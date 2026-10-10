import { useEffect, useRef, useState } from "react";
import {
  BACKEND_OFFLINE_EVENT,
  BACKEND_ONLINE_EVENT,
  backendHealthUrl,
} from "../../transport/http/client";

/**
 * 本地后端失联提示页（整屏遮罩）。
 *
 * 触发：transport/http/client 在 fetch 网络层失败（连接被拒）时广播
 * `mainpg:backend-offline`。最常见的成因是本地后端子进程被杀毒软件拦截/清除，
 * 页面还活着但所有接口都打不通——与其白屏，不如明说原因和自救办法。
 * 恢复：任意一次成功的 HTTP 响应广播 `mainpg:backend-online` 自动收起；
 * 或用户处理完杀软拦截后点「重试」。
 *
 * 注意：「重试」只重新探测后端是否已恢复，**不会拉起被杀的进程**。若后端进程
 * 已被安全软件终止，必须回启动器点「重启主程序」或完全退出重开。
 */
export function BackendOfflineNotice() {
  const [offline, setOffline] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    const goOffline = () => {
      // 防抖 1s：批量请求连环失败只弹一次。
      if (timerRef.current !== null) return;
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        setOffline(true);
      }, 1000);
    };
    const goOnline = () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
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

  if (!offline) return null;

  const retry = async () => {
    setRetrying(true);
    try {
      // 探后端真实健康端点：打包版同源直连 8010；开发态由 vite 代理 /health 转发。
      const response = await fetch(backendHealthUrl(), { cache: "no-store" });
      // 必须校验返回 JSON：开发态 /health 若未被代理，vite 会回 SPA 的 index.html(200)，
      // 只认 response.ok 会把这种「假成功」当成后端已恢复。
      const contentType = response.headers.get("content-type") ?? "";
      if (response.ok && contentType.includes("application/json")) {
        setOffline(false);
        window.location.reload();
        return;
      }
    } catch {
      // 仍然失联，保持提示页。
    } finally {
      setRetrying(false);
    }
  };

  return (
    <div className="backend-offline-mask" role="alertdialog" aria-modal="true" aria-labelledby="backend-offline-title">
      <div className="backend-offline-card">
        <div className="backend-offline-icon" aria-hidden="true">!</div>
        <h2 id="backend-offline-title">本地服务组件无响应</h2>
        <p>
          程序界面还在，但本地后台服务连不上了。最常见的原因是
          <b>杀毒软件拦截了 MainPG 的后台组件</b>，也可能是组件意外退出。
        </p>
        <ol>
          <li>打开杀毒软件（电脑管家/360/火绒等），把 MainPG 安装目录加入<b>信任区</b>；</li>
          <li>回到启动器点「重启主程序」（或完全退出 MainPG 后重新打开）；</li>
          <li>「重试」只能重新检测后端是否已恢复，不会重启后端进程。</li>
        </ol>
        <button type="button" className="backend-offline-retry" disabled={retrying} onClick={retry}>
          {retrying ? "正在重试…" : "重试"}
        </button>
      </div>
    </div>
  );
}
