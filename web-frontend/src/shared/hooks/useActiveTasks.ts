import { useCallback, useEffect, useRef, useState } from "react";

import { getAuthAccount, getAuthToken } from "../../transport/http/client";

/** 与后端 /api/tasks/active-overview 的 item 结构对应。 */
export type ActiveTaskItem = {
  module: string;
  moduleLabel: string;
  key: string;
  title: string;
  status: string;
  /** done/total 可能为 null：该模块算不出可靠进度时前端显示不确定态。 */
  done: number | null;
  total: number | null;
  updatedAt: string;
};

/** 最近结束的任务：只用来判断「刚刚从处理中消失的那几项」。 */
export type FinishedTaskItem = {
  key: string;
  module: string;
  title: string;
  status: string;
  ok: boolean;
};

export type JustFinishedTask = FinishedTaskItem & { at: number };

/** 轮询间隔：任务进度不需要秒级，4 秒既跟得上又不折腾后端。 */
const POLL_MS = 4000;

/**
 * 完成/失败记录保留多久。
 *
 * 这里是**兜底**，不是主要清理手段：正常情况下用户点掉 × 就没了。
 * 留 5 分钟是为了避免「用户没注意到、记录永远挂在顶栏」——真挂一整天更烦人。
 */
const FINISHED_KEEP_MS = 5 * 60 * 1000;

type Overview = { items: ActiveTaskItem[]; finished: FinishedTaskItem[] };

async function fetchOverview(): Promise<Overview | null> {
  try {
    const account = getAuthAccount<{ workspace_id?: string; workspace_code?: string }>() ?? {};
    const headers: Record<string, string> = {
      "X-Workspace-ID": account.workspace_id || account.workspace_code || "local",
    };
    const token = getAuthToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch("/api/tasks/active-overview", { headers });
    // 请求失败返回 null（≠ 空列表）：空列表意味着「确实没任务了」，
    // 而网络抖动 / 5xx / 未登录不该被解读成任务消失，否则按钮会闪。
    if (!response.ok) return null;
    const data = (await response.json().catch(() => null)) as {
      items?: unknown;
      finished?: unknown;
    } | null;
    if (!data) return null;
    return {
      items: Array.isArray(data.items) ? (data.items as ActiveTaskItem[]) : [],
      finished: Array.isArray(data.finished) ? (data.finished as FinishedTaskItem[]) : [],
    };
  } catch {
    return null;
  }
}

/**
 * 全局处理中任务清单 + 「刚完成」判定。
 *
 * 完成事件靠**后端两次快照的差集**推出来：上一轮还在 items 里、这一轮没了，
 * 说明它刚结束；再去 finished 里查它的最终状态，才知道是真成功还是失败。
 * 不能只看「有没有任务」——那样跑完之后按钮会立刻消失，用户什么都看不到。
 *
 * 失败同样会进列表（顶部用琥珀色区分），因为失败比成功更需要被看到。
 * 每条完成记录都能被用户手动 × 掉；没人管的话 5 分钟后兜底清理。
 */
export function useActiveTasks(enabled = true) {
  const [items, setItems] = useState<ActiveTaskItem[]>([]);
  const [justFinished, setJustFinished] = useState<JustFinishedTask[]>([]);
  const prevKeysRef = useRef<Set<string> | null>(null);

  const dismissFinished = useCallback((key: string) => {
    setJustFinished((old) => old.filter((item) => item.key !== key));
  }, []);

  useEffect(() => {
    if (!enabled) {
      setItems([]);
      setJustFinished([]);
      prevKeysRef.current = null;
      return;
    }
    let cancelled = false;

    const tick = async () => {
      if (document.hidden) return;
      const next = await fetchOverview();
      // 拿不到数据就保持原样：不清空、不更新快照，下一轮再试。
      if (cancelled || next === null) return;

      const currentKeys = new Set(next.items.map((item) => item.key));
      const prevKeys = prevKeysRef.current;
      if (prevKeys) {
        const gone = [...prevKeys].filter((key) => !currentKeys.has(key));
        if (gone.length) {
          const byKey = new Map(next.finished.map((item) => [item.key, item]));
          const fresh = gone
            .map((key) => byKey.get(key))
            .filter((item): item is FinishedTaskItem => Boolean(item))
            .map((item) => ({ ...item, at: Date.now() }));
          if (fresh.length) {
            const freshKeys = new Set(fresh.map((item) => item.key));
            setJustFinished((old) => [
              ...old.filter((item) => !freshKeys.has(item.key)),
              ...fresh,
            ]);
          }
        }
      }
      prevKeysRef.current = currentKeys;
      setItems(next.items);
      // 兜底清理：用户一直不点 × 的记录，5 分钟后自己退场。
      setJustFinished((old) => old.filter((item) => Date.now() - item.at < FINISHED_KEEP_MS));
    };

    void tick();
    const timer = window.setInterval(tick, POLL_MS);
    // 从后台切回前台时立刻补一次，避免最长 4 秒的滞后。
    const onVisible = () => {
      if (!document.hidden) void tick();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [enabled]);

  return { items, justFinished, dismissFinished };
}
