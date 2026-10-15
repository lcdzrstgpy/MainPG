import { useEffect } from "react";

/**
 * 任务运行期间持有一个 Web Lock：这是浏览器认可的「本页在后台仍要干活」信号，
 * 用来豁免它们对隐藏页的两种压制 ——
 *  1) 密集节流：Chrome/Edge 隐藏满 5 分钟后把定时器压到约 1 分钟一次
 *     （见 Chrome 88 起的 intensive wake-up throttling，持有 Web Lock 的页面被豁免）；
 *  2) 冻结：Chrome 133+ 节能模式会冻结后台页，条件之一是「页面没有持有 Web Lock」。
 *
 * 为什么非管不可：任务页 1 秒一次轮询 /outputs 就是「任务心跳」，后端超过 180 秒收不到
 * 心跳会判定页面已关、把任务自动暂停。被节流到 1 分钟一次时余量只剩几十秒 —— 用户只是
 * 切到别的界面、点了别的页签或最小化窗口，任务就可能被误停（工单来源）。
 * 持有锁后轮询照常跑，心跳不断。
 *
 * 锁在页面卸载（关页签/关工作台/崩溃）时自动释放，成本保护不受影响：页面真的没了，
 * 轮询自然停止，180 秒后照常自动暂停。
 */
export function useTaskHeartbeatKeepAlive(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const locks = navigator.locks;
    // 老浏览器没有 Web Locks：保持原行为（隐藏即被节流），由后端 180 秒阈值兜底。
    if (!locks || typeof locks.request !== "function") return;
    const controller = new AbortController();
    void locks
      // shared：多个处理页签可以同时持有（exclusive 会让后来的页签排队等锁）。
      .request("mainpg:product-processing-task", { mode: "shared", signal: controller.signal }, () => new Promise<void>(() => undefined))
      // 释放（任务结束 / 页面卸载）会以 AbortError 结束，属正常路径，不打扰用户。
      .catch(() => undefined);
    return () => controller.abort();
  }, [active]);
}
