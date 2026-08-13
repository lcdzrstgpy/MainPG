type FinalizeRunLike = {
  id: string;
  status: string;
};

type PrecheckFinalizeRefreshOptions<T extends FinalizeRunLike> = {
  fetchRun: (runId: string) => Promise<T>;
  onRun: (run: T) => void;
  onError: (error: unknown) => void;
  intervalMs?: number;
};

export type PrecheckFinalizeRefresh = {
  watch: (runId: string) => void;
  setVisible: (visible: boolean) => void;
  stop: () => void;
};

const TERMINAL_STATUSES = new Set(["completed", "publish_failed", "stale"]);

export function createPrecheckFinalizeRefresh<T extends FinalizeRunLike>({
  fetchRun,
  onRun,
  onError,
  intervalMs = 1500,
}: PrecheckFinalizeRefreshOptions<T>): PrecheckFinalizeRefresh {
  let watchedRunId: string | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let generation = 0;
  let visible = true;
  let stopped = false;
  let inFlight = false;
  let refreshPending = false;

  const clearTimer = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };

  const schedule = (requestGeneration: number) => {
    clearTimer();
    if (
      stopped
      || !visible
      || watchedRunId === null
      || requestGeneration !== generation
    ) return;
    timer = setTimeout(() => {
      timer = null;
      void refresh();
    }, intervalMs);
  };

  const refresh = async () => {
    if (stopped || !visible || watchedRunId === null) return;
    if (inFlight) {
      refreshPending = true;
      return;
    }

    const requestGeneration = generation;
    const requestedRunId = watchedRunId;
    let shouldSchedule = false;
    inFlight = true;
    refreshPending = false;

    try {
      const run = await fetchRun(requestedRunId);
      if (
        stopped
        || !visible
        || requestGeneration !== generation
        || watchedRunId !== requestedRunId
      ) return;
      onRun(run);
      if (TERMINAL_STATUSES.has(run.status)) {
        watchedRunId = null;
        clearTimer();
      } else {
        shouldSchedule = true;
      }
    } catch (error) {
      if (
        stopped
        || !visible
        || requestGeneration !== generation
        || watchedRunId !== requestedRunId
      ) return;
      onError(error);
      shouldSchedule = true;
    } finally {
      inFlight = false;
      if (stopped) return;
      if (refreshPending || requestGeneration !== generation) {
        refreshPending = false;
        if (visible && watchedRunId !== null) void refresh();
      } else if (shouldSchedule) {
        schedule(requestGeneration);
      }
    }
  };

  return {
    watch(runId: string) {
      if (stopped || !runId) return;
      generation += 1;
      watchedRunId = runId;
      refreshPending = true;
      clearTimer();
      if (visible) void refresh();
    },
    setVisible(nextVisible: boolean) {
      if (stopped || visible === nextVisible) return;
      visible = nextVisible;
      generation += 1;
      clearTimer();
      refreshPending = watchedRunId !== null;
      if (visible && watchedRunId !== null) void refresh();
    },
    stop() {
      if (stopped) return;
      stopped = true;
      generation += 1;
      watchedRunId = null;
      refreshPending = false;
      clearTimer();
    },
  };
}
