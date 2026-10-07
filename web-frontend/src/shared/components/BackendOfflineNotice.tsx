import { useEffect, useRef, useState } from "react";
import {
  BACKEND_OFFLINE_EVENT,
  BACKEND_ONLINE_EVENT,
} from "../../transport/http/client";

/**
 * 本地后端失联提示页（整屏遮罩）。
 *
 * 触发：transport/http/client 在 fetch 网络层失败（连接被拒）时广播
 * `mainpg:backend-offline`。最常见的成因是本地后端子进程被杀毒软件拦截/清除，
 * 页面还活着但所有接口都打不通——与其白屏，不如明说原因和自救办法。
 * 恢复：任意一次成功的 HTTP 响应广播 `mainpg:backend-online` 自动收起；
 * 或用户处理完杀软拦截后点「重试」。
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
      // 用 /docs 探活：FastAPI 恒有此端点，200 即本地后端已恢复。
      const response = await fetch("/docs", { cache: "no-store" });
      if (response.ok) {
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
          <li>完全退出 MainPG 后重新打开；</li>
          <li>仍未恢复请点下方「重试」。</li>
        </ol>
        <button type="button" className="backend-offline-retry" disabled={retrying} onClick={retry}>
          {retrying ? "正在重试…" : "重试"}
        </button>
      </div>
    </div>
  );
}
