// 运行中任务登记表（模块级，不依赖 React 生命周期）。
//
// 为什么需要它：任务页每秒轮询 `/tasks/{id}/outputs` 就是后端认可的"用户还在看"心跳。
// 但页面一旦被整页刷新（失联自动刷新、手动刷新）就会丢掉应用内标签页（内存态）、
// 处理页被关掉 → 心跳断 → 后端 90 秒后把任务自动暂停（2026-10-15 线上复现）。
//
// 处理页在轮询期间登记自己，应用壳据此：
//   1) 只要工作台还开着，就替这些任务续心跳（见 WorkspaceShell 的 watchdog）；
//   2) 失联恢复时不再整页 reload（见 BackendOfflineNotice），从根上避免标签被清。
const runningTaskIdsByRef = new Map<number, number>();

/** 登记一个正在运行（被本页盯着）的任务，返回取消登记的清理函数。 */
export function registerRunningTask(taskId: number): () => void {
  const id = Number(taskId);
  if (!Number.isFinite(id)) return () => {};
  runningTaskIdsByRef.set(id, (runningTaskIdsByRef.get(id) ?? 0) + 1);
  return () => {
    const next = (runningTaskIdsByRef.get(id) ?? 0) - 1;
    if (next > 0) runningTaskIdsByRef.set(id, next);
    else runningTaskIdsByRef.delete(id);
  };
}

/** 当前被页面盯着的运行中任务。 */
export function runningTaskIds(): number[] {
  return [...runningTaskIdsByRef.keys()];
}

/** 是否还有运行中任务（用于决定"能不能整页刷新"）。 */
export function hasRunningTask(): boolean {
  return runningTaskIdsByRef.size > 0;
}
