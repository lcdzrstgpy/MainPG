import { act, createElement, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useTaskFeed, TASK_ACTIVE_POLL_MS, TASK_IDLE_POLL_MS, TASK_REQUEST_TIMEOUT_MS } from "@/lib/hooks/use-task-feed";

let root: Root;
let container: HTMLDivElement;
let current: ReturnType<typeof useTaskFeed>;
let visibility: "visible" | "hidden";
const empty = { active: [], attention: [], recent: [] };
const busy = { ...empty, active: [{ kind: "compose", id: "new-task", projectId: "project-a" }] };
const fetchMock = vi.fn();
function Probe() {
  const value = useTaskFeed();
  useEffect(() => { current = value; }, [value]);
  return null;
}
const reply = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
async function tick(ms = 0) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
async function mount() { await act(async () => root.render(createElement(Probe))); await tick(); }

beforeEach(() => {
  vi.useFakeTimers();
  visibility = "visible";
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset().mockImplementation(async () => reply(empty));
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("任务中心后台观察", () => {
  it("空列表仍发现后来启动的任务，发现后提高刷新频率", async () => {
    await mount();
    expect(current.feed.active).toEqual([]);
    fetchMock.mockImplementation(async () => reply(busy));
    await tick(TASK_IDLE_POLL_MS);
    expect(current.feed.active[0]?.id).toBe("new-task");
    await tick(TASK_ACTIVE_POLL_MS);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("网络或响应格式错误保留旧状态，并允许手动重试恢复", async () => {
    fetchMock.mockResolvedValueOnce(reply(busy));
    await mount();
    const updatedAt = current.updatedAt;
    fetchMock.mockRejectedValueOnce(new Error("offline"));
    await act(async () => current.refresh());
    expect(current).toMatchObject({ error: true, loading: false, updatedAt });
    expect(current.feed.active).toHaveLength(1);
    fetchMock.mockResolvedValueOnce(reply({ error: "unavailable" }));
    await act(async () => current.refresh());
    expect(current.error).toBe(true);
    expect(current.feed.active).toHaveLength(1);
    await act(async () => current.refresh());
    expect(current).toMatchObject({ error: false, feed: empty });
  });

  it("页面隐藏时暂停；返回、重新联网和获得焦点时立即读取", async () => {
    await mount();
    visibility = "hidden";
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    await tick(TASK_IDLE_POLL_MS * 3);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    visibility = "visible";
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => window.dispatchEvent(new Event("online")));
    await act(async () => window.dispatchEvent(new Event("focus")));
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("多个刷新入口不重叠读取，超时中止后可恢复", async () => {
    let signal: AbortSignal | undefined;
    fetchMock.mockImplementationOnce((_url, options) => new Promise((_resolve, reject) => {
      signal = options.signal;
      signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    }));
    await mount();
    await act(async () => { current.refresh(); window.dispatchEvent(new Event("focus")); current.refresh(); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await tick(TASK_REQUEST_TIMEOUT_MS);
    expect(signal?.aborted).toBe(true);
    expect(current).toMatchObject({ error: true, loading: false, updatedAt: null });
    await act(async () => current.refresh());
    expect(current.error).toBe(false);
  });
});
