// 桌面端后端存活心跳：每个浏览器标签页使用独立 ID，独立于任何业务组件。
//
// 只要这段 JS 被加载执行就会上报心跳。放在主入口独立调用，而不是挂在 App
// 组件 useEffect 里——避免业务组件渲染失败时心跳也丢失，导致后端"关页即停"
// 看门狗把刚启动的后端误杀（用户只见界面无响应、后端端口没有）。
let clientId = "";
let eventSeq = 0;

function runtimeUrl(action: "heartbeat" | "bye"): string {
  eventSeq += 1;
  const query = new URLSearchParams({
    client_id: clientId,
    event_seq: String(eventSeq),
  });
  return `/api/runtime/${action}?${query.toString()}`;
}

function sendHeartbeat(): void {
  try {
    navigator.sendBeacon(runtimeUrl("heartbeat"), "{}");
  } catch {
    // 发送失败忽略，后端有 idle 兜底
  }
}

function sendBye(event: PageTransitionEvent): void {
  // 进入浏览器前进/后退缓存并不代表标签页关闭，恢复后仍需继续使用后端。
  if (event.persisted) return;
  try {
    navigator.sendBeacon(runtimeUrl("bye"), "{}");
  } catch {
    // 忽略
  }
}

export function startRuntimeHeartbeat(): void {
  clientId =
    typeof globalThis.crypto?.randomUUID === "function"
      ? globalThis.crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  sendHeartbeat();
  const timer = window.setInterval(sendHeartbeat, 15_000);
  window.addEventListener("pageshow", sendHeartbeat);
  window.addEventListener("pagehide", sendBye);
  // 切回标签页 / 从冻结恢复时立刻补发一次。
  //
  // 浏览器会把后台标签的定时器节流，内存吃紧时甚至整个冻结——心跳一停，后端看门狗
  // 就把「没心跳」当成「用户走了」，15 分钟后自杀并把正在跑的任务取消、按 50% 结算。
  // pageshow 只在 bfcache 恢复时触发，普通切标签根本不会走，所以这里必须单独补。
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") sendHeartbeat();
  });
  window.addEventListener("online", sendHeartbeat);
  // 无需清理：心跳随标签页关闭自然失效，后端由 idle 兜底回收。
  void timer;
}
