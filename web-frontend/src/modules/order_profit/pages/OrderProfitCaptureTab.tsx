import { useCallback, useEffect, useRef, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import { orderProfitApi } from "../api/orderProfitApi";
import { CaptureCenterCard } from "../components/CaptureCenterCard";
import { ShopDataGlyph } from "../components/ShopDataGlyph";
import { ShopDataScene } from "../components/ShopDataScene";
import { EmptyBlock } from "../components/ShopDataPrimitives";
import { TeamRollupDialog } from "../components/TeamRollupDialog";
import {
  captureStatusChip,
  isActiveCaptureStatus,
  resolveSyncKind,
  RETRYABLE_CAPTURE_STEPS,
  summarizeCaptureResults,
} from "../data/captureTaskModel";
import {
  clearActiveCaptureTask,
  loadActiveCaptureTask,
  loadLastFullSyncAt,
  saveActiveCaptureTask,
  saveLastFullSyncAt,
} from "../data/captureTaskStorage";
import { money, numberText } from "../format";
import type {
  CaptureRunSummary,
  OrderProfitCaptureStatus,
  OrderProfitSettings,
  PluginSession,
  ProfitShop,
  TeamRollupPreview,
  TeamRollupSubmitResult,
} from "../types";

export function OrderProfitCaptureTab({
  isActive,
  onCaptured,
  onOpenAccounts,
  refreshToken = 0,
}: {
  isActive: boolean;
  onCaptured: () => void;
  onOpenAccounts?: () => void;
  /** 页头切换店铺后自增，用来重新拉取当前采集店铺。 */
  refreshToken?: number;
}) {
  const [sessions, setSessions] = useState<PluginSession[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [settings, setSettings] = useState<OrderProfitSettings>({
    usd_cny_rate: 7.2,
    commission_rate: 0.05,
    head_unit_price_cny: 0,
  });
  const [rateInput, setRateInput] = useState("7.2");
  const [commissionInput, setCommissionInput] = useState("5");
  const [headInput, setHeadInput] = useState("0");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<OrderProfitCaptureStatus | null>(null);
  const [activeShop, setActiveShop] = useState<ProfitShop | null>(null);
  const [shopBusy, setShopBusy] = useState(false);
  const [lastFullAt, setLastFullAt] = useState(0);
  const [recentRuns, setRecentRuns] = useState<CaptureRunSummary[]>([]);
  const [recentLoading, setRecentLoading] = useState(false);
  const [submitBusy, setSubmitBusy] = useState(false);
  const [submitText, setSubmitText] = useState("");
  const [rollupPreview, setRollupPreview] = useState<TeamRollupPreview | null>(null);
  const [rollupResult, setRollupResult] = useState<TeamRollupSubmitResult | null>(null);
  const pollTimer = useRef<number | null>(null);
  // 记住当前指令号：刚下发时 status 还没回来，终止按钮也要能停。
  const activeCommandIdRef = useRef(0);

  /** 采集前重新拉一次会话：插件重连会换新 session，用旧 id 会让指令一直排队。 */
  const resolveSessionId = async (): Promise<string> => {
    try {
      const payload = await orderProfitApi.listSessions();
      const list = payload.sessions || [];
      setSessions(list);
      const newestOnline = list.find((item) => item.online);
      if (newestOnline) {
        setSessionId(newestOnline.session_id);
        return newestOnline.session_id;
      }
    } catch {
      /* 拉取失败就用当前选择兜底 */
    }
    return sessionId;
  };

  const clearPoll = () => {
    if (pollTimer.current !== null) {
      window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  };

  const refreshRecent = useCallback(async () => {
    setRecentLoading(true);
    try {
      const payload = await orderProfitApi.recentCaptures(6);
      setRecentRuns(payload.items || []);
    } catch {
      // 采集中心读取失败不影响采集本身，保留上一次结果。
    } finally {
      setRecentLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isActive) void refreshRecent();
  }, [isActive, refreshRecent, refreshToken]);

  useEffect(() => {
    if (!isActive) {
      clearPoll();
      return;
    }
    let cancelled = false;
    setError("");
    Promise.all([
      orderProfitApi.listSessions(),
      orderProfitApi.getSettings(),
      orderProfitApi.listShops(),
    ])
      .then(([sessionPayload, settingsPayload, shopPayload]) => {
        if (cancelled) return;
        const list = sessionPayload.sessions || [];
        setSessions(list);
        setSessionId((current) => {
          // 始终跟随最新在线会话：插件重连后 id 会变，沿用旧 id 会让指令卡在「排队中」。
          const newestOnline = list.find((item) => item.online);
          if (newestOnline) return newestOnline.session_id;
          return current || list[0]?.session_id || "";
        });
        setActiveShop(shopPayload.active || null);
        // 接口回包里没有 settings（后端异常时可能回空对象、老版本后端也可能没这个字段）时
        // 不能直接解引用 —— 一抛就是整页白屏（渲染期 TypeError 只能靠错误边界兜）。
        // 三个字段逐个兜默认值，和后端 repository.settings() 的建表默认保持一致。
        const rawSettings = settingsPayload?.settings;
        const nextSettings: OrderProfitSettings = {
          usd_cny_rate: Number(rawSettings?.usd_cny_rate ?? 7.2) || 7.2,
          commission_rate: Number(rawSettings?.commission_rate ?? 0.05),
          head_unit_price_cny: Number(rawSettings?.head_unit_price_cny ?? 0) || 0,
        };
        setSettings(nextSettings);
        setRateInput(String(nextSettings.usd_cny_rate));
        setCommissionInput(String(nextSettings.commission_rate * 100));
        setHeadInput(String(nextSettings.head_unit_price_cny));
      })
      .catch((cause) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      cancelled = true;
      clearPoll();
    };
  }, [isActive, refreshToken]);

  useEffect(() => () => clearPoll(), []);

  // “上次全量”按店铺分别记录：切到从未全量过的店铺时必须重新走全量。
  useEffect(() => {
    setLastFullAt(loadLastFullSyncAt(activeShop?.name || ""));
  }, [activeShop?.name]);

  /** 采集进入终态时清掉持久化任务；全量成功才刷新“上次全量”时间。 */
  const finishTask = (commandId: number, success: boolean) => {
    const task = loadActiveCaptureTask();
    if (!task || task.commandId !== commandId) return;
    if (success && task.kind === "full") {
      const now = Date.now();
      saveLastFullSyncAt(now, task.shopName || activeShop?.name || "");
      setLastFullAt(now);
    }
    clearActiveCaptureTask(commandId);
  };

  const pollStatus = async (commandId: number) => {
    try {
      const next = await orderProfitApi.captureStatus(commandId);
      setStatus(next);
      if (next.status === "succeeded") {
        setBusy(false);
        finishTask(commandId, next.ok);
        void refreshRecent();
        if (next.command_type === "temu_capture_all" || next.command_type === "temu_capture_incremental") {
          showToast(next.status_text || "采集完成", next.ok ? "success" : "error");
          if (next.ok) onCaptured();
        } else if (next.command_type === "temu_capture_resource") {
          showToast(next.status_text || "补采完成", next.ok ? "success" : "error");
          if (next.ok) onCaptured();
        } else if (next.ok && next.ingested) {
          showToast(`采集完成：${next.order_count} 单 / ${next.sub_order_count} 条子订单`, "success");
          onCaptured();
        } else {
          showToast(next.status_text || next.error || "没有识别到订单数据", "error");
        }
        return;
      }
      if (next.status === "failed" || next.status === "cancelled") {
        setBusy(false);
        finishTask(commandId, false);
        void refreshRecent();
        showToast(
          next.status_text || next.error || (next.status === "cancelled" ? "采集已终止" : "订单采集失败"),
          next.status === "cancelled" ? "info" : "error",
        );
        if (next.status === "cancelled") {
          // 终止前已完成的部分已经入库，通知采集记录页刷新。
          onCaptured();
        }
        return;
      }
      pollTimer.current = window.setTimeout(() => void pollStatus(commandId), 1500);
    } catch (cause) {
      setBusy(false);
      clearActiveCaptureTask(commandId);
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    }
  };

  /** 等待单条补采指令结束；不用主轮询定时器，用户终止后也能及时退出。 */
  const waitForCommand = async (commandId: number): Promise<OrderProfitCaptureStatus | null> => {
    for (let attempt = 0; attempt < 200; attempt += 1) {
      try {
        const next = await orderProfitApi.captureStatus(commandId);
        setStatus(next);
        if (next.status === "succeeded" || next.status === "failed" || next.status === "cancelled") {
          finishTask(commandId, next.status === "succeeded" && next.ok);
          return next;
        }
      } catch {
        clearActiveCaptureTask(commandId);
        return null;
      }
      await new Promise<void>((resolve) => window.setTimeout(resolve, 1500));
    }
    return null;
  };

  const retryFailedSteps = async (run: Pick<CaptureRunSummary, "results" | "shop_name">) => {
    if (busy) return;
    const failed = run.results.filter(
      (row) => row.state === "failed" && RETRYABLE_CAPTURE_STEPS.has(row.key),
    );
    if (!failed.length) return;
    const activeSession = await resolveSessionId();
    if (!activeSession) {
      showToast("请先在插件弹窗点「连接插件」，并保持 Temu 页面打开", "error");
      return;
    }
    clearPoll();
    setBusy(true);
    setError("");
    let done = 0;
    let stopped = false;
    try {
      for (const row of failed) {
        try {
          const command = await orderProfitApi.startResourceCapture(activeSession, row.key);
          activeCommandIdRef.current = command.command_id;
          saveActiveCaptureTask({
            commandId: command.command_id,
            kind: "resource",
            label: `补采 ${row.label || row.key}`,
            shopName: run.shop_name || activeShop?.name || "",
            startedAt: Date.now(),
          });
          const result = await waitForCommand(command.command_id);
          if (!result || result.status === "cancelled") {
            stopped = true;
            break;
          }
          done += 1;
        } catch (cause) {
          // 某一项下发失败不阻塞后续步骤，逐项提示后继续。
          showToast(
            `${row.label || row.key}：${cause instanceof Error ? cause.message : String(cause)}`,
            "error",
          );
        }
      }
      if (!stopped) showToast(`补采结束：已执行 ${done}/${failed.length} 项`, "success");
    } finally {
      setBusy(false);
      activeCommandIdRef.current = 0;
      await refreshRecent();
      onCaptured();
    }
  };

  // 页面切走/刷新后回到采集页：恢复仍在跑的任务，而不是显示“还没有采集任务”。
  const restoredCommandRef = useRef(0);
  useEffect(() => {
    if (!isActive) return;
    const task = loadActiveCaptureTask();
    if (!task || restoredCommandRef.current === task.commandId) return;
    restoredCommandRef.current = task.commandId;
    let cancelled = false;
    const restore = async () => {
      try {
        const next = await orderProfitApi.captureStatus(task.commandId);
        if (cancelled) return;
        if (isActiveCaptureStatus(next.status)) {
          setStatus(next);
          setBusy(true);
          await pollStatus(task.commandId);
        } else {
          clearActiveCaptureTask(task.commandId);
        }
      } catch {
        if (!cancelled) clearActiveCaptureTask(task.commandId);
      }
    };
    void restore();
    return () => {
      cancelled = true;
    };
  }, [isActive]);

  const startSync = async (requested: "auto" | "full" | "incremental") => {
    if (busy) return;
    if (!activeShop) {
      showToast("请先绑定并选中目标店铺，再开始采集", "error");
      return;
    }
    const activeSession = await resolveSessionId();
    if (!activeSession) {
      showToast("请先在插件弹窗点「连接插件」，并保持 Temu 页面打开", "error");
      return;
    }
    const incremental = requested === "auto"
      ? resolveSyncKind({ now: Date.now(), lastFullAt }) === "incremental"
      : requested === "incremental";
    clearPoll();
    setBusy(true);
    setStatus(null);
    setError("");
    try {
      const command = await orderProfitApi.startCaptureAll(activeSession, incremental, activeShop.name);
      activeCommandIdRef.current = command.command_id;
      saveActiveCaptureTask({
        commandId: command.command_id,
        kind: incremental ? "incremental" : "full",
        label: incremental ? "增量同步" : "全量同步",
        shopName: activeShop.name,
        startedAt: Date.now(),
      });
      showToast(
        incremental
          ? "已开始增量同步：只补最新数据，失败项可在下方单独重试"
          : "已开始全量同步：依次采集 6 类数据，采集中可以离开本页",
        "info",
      );
      await pollStatus(command.command_id);
    } catch (cause) {
      setBusy(false);
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
      showToast(message, "error");
    }
  };

  const stopCapture = async () => {
    const commandId = activeCommandIdRef.current || status?.command_id || 0;
    if (!commandId) {
      showToast("当前没有正在进行的采集", "info");
      return;
    }
    // 先让界面立刻停下来，别等插件把当前页面跑完。
    clearPoll();
    setBusy(false);
    setStatus((prev) => (prev ? { ...prev, status: "cancelled" } : prev));
    try {
      await orderProfitApi.cancelCapture(commandId);
      showToast("已终止：插件会在当前步骤结束后停下，已完成的数据保留", "info");
      onCaptured();
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    }
  };

  const syncShopCode = async (shopId: string) => {
    if (!shopId) return;
    setShopBusy(true);
    try {
      const payload = await orderProfitApi.syncShopCode(shopId);
      setActiveShop(payload.shop || null);
      showToast(`店铺码已同步为 ${payload.shop?.store_code || ""}`, "success");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setShopBusy(false);
    }
  };

  /** 先拉一次预览，弹窗里确认「哪些店铺 → 哪个服务器账号」后再真正提交。 */
  const openRollupDialog = async () => {
    if (submitBusy) return;
    setSubmitBusy(true);
    try {
      const preview = await orderProfitApi.teamRollupPreview();
      setRollupPreview(preview);
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setSubmitBusy(false);
    }
  };

  const submitTeamRollup = async (weekStart: string) => {
    setSubmitBusy(true);
    try {
      const payload = await orderProfitApi.submitTeamRollup(weekStart);
      setRollupPreview(null);
      setRollupResult(payload);
      const pending = payload.pending_local_shops?.length || 0;
      const text = payload.ok
        ? `已提交 ${payload.week_start} 的本周汇总：${payload.stores?.length ?? 0} 家店铺、新增 ${payload.created ?? 0} 天、更新 ${payload.updated ?? 0} 天、跳过 ${payload.stale ?? 0} 天`
          + (pending ? `；还有 ${pending} 家店铺没有店铺码，未提交` : "")
        : payload.status_text || "没有可提交的数据";
      setSubmitText(text);
      showToast(text, payload.ok ? "success" : "info");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setSubmitBusy(false);
    }
  };

  /** 单步重试：换个资源采集命令，只补被跳过/失败的那一块。 */
  const retryStep = async (resource: string, label: string) => {
    if (busy) return;
    const activeSession = await resolveSessionId();
    if (!activeSession) {
      showToast("请先在插件弹窗点「连接插件」", "error");
      return;
    }
    clearPoll();
    setBusy(true);
    setStatus(null);
    try {
      const command = await orderProfitApi.startResourceCapture(activeSession, resource);
      activeCommandIdRef.current = command.command_id;
      saveActiveCaptureTask({
        commandId: command.command_id,
        kind: "resource",
        label: `补采 ${label}`,
        shopName: activeShop?.name || "",
        startedAt: Date.now(),
      });
      showToast(`已重新采集「${label}」`, "info");
      await pollStatus(command.command_id);
    } catch (cause) {
      setBusy(false);
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    }
  };

  // 采集进行中先按插件上报的步骤逐条显示，结束后以最终结果为准。
  const liveSteps = status?.progress?.steps || [];
  const finalSteps = status?.results || [];
  const stepRows = finalSteps.length ? finalSteps : liveSteps;
  const resultSummary = summarizeCaptureResults(status?.results);
  const failedSteps = (status?.results || []).filter(
    (row) => row.state === "failed" && RETRYABLE_CAPTURE_STEPS.has(row.key),
  );
  const statusChip = status
    ? captureStatusChip(status.status, status.ok, status.results)
    : { label: "未开始", tone: "" as const };
  // 指令结束后强制收口到 100%：最后一条进度可能停在“最后一步运行中”（例如 5/6 = 83%）。
  const progressPercent = status
    ? status.status === "succeeded" ? 100 : status.progress?.percent ?? 0
    : 0;
  const progressLabel = status?.status === "succeeded"
    ? "采集完成"
    : status?.progress?.step_label || "采集中";
  const progressStepText = status?.status === "succeeded" || !status?.progress?.step_total
    ? ""
    : `（${status.progress.step}/${status.progress.step_total}）`;
  const nextSyncKind = resolveSyncKind({ now: Date.now(), lastFullAt });

  const saveSettings = async () => {
    try {
      const payload = await orderProfitApi.saveSettings({
        usd_cny_rate: Number(rateInput) || 0,
        commission_rate: (Number(commissionInput) || 0) / 100,
        head_unit_price_cny: Number(headInput) || 0,
      });
      setSettings(payload.settings);
      showToast("费率已保存，后续采集/预估按新费率计算", "success");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    }
  };

  const selected = sessions.find((item) => item.session_id === sessionId);

  return (
    <section className="op-view">
      {error ? <div className="op-alert is-error">{error}</div> : null}

      <div className="op-capture-layout">
        <div className="op-card op-capture-panel">
          <div className="op-panel-head">
            <div className="op-step-copy">
              <span className="op-step-label">第 1 步</span>
              <ShopDataGlyph name="source" className="op-step-glyph" />
              <strong>准备采集</strong>
            </div>
            <span className="op-chip">6 类数据</span>
          </div>
          <p className="op-hint">确认插件在线、写入店铺正确，再开始同步；采集期间可以离开本页。</p>

          <div className="op-preflight" aria-label="采集前置检查">
            <span className={`op-chip ${selected?.online ? "is-ok" : "is-warn"}`}>
              插件{selected?.online ? "在线" : "未连接"}
            </span>
            <span className={`op-chip ${activeShop ? "is-ok" : "is-warn"}`}>
              {activeShop ? `写入：${activeShop.name}` : "写入店铺未绑定"}
            </span>
            <span className={`op-chip ${lastFullAt ? "" : "is-warn"}`}>
              {lastFullAt ? `上次全量：${formatDay(lastFullAt)}` : "尚未全量同步"}
            </span>
          </div>
          {!selected?.online ? (
            <p className="op-hint">
              插件未连接：在浏览器工具栏打开「界野采集助手」，点「连接插件」；设备配对在「系统设置 → 插件设置」。
            </p>
          ) : null}

          <ShopDataScene variant="prepare" className="op-capture-scene" />

          <div className="op-shop-block">
            <div className="op-panel-head">
              <strong>店铺绑定</strong>
              <span className={`op-chip ${activeShop ? "is-ok" : "is-warn"}`}>
                {activeShop
                  ? `当前：${activeShop.name}${activeShop.store_code ? ` · ${activeShop.store_code}` : ""}`
                  : "未绑定"}
              </span>
            </div>
            <p className="op-hint">
              采集写入「{activeShop?.name || "未绑定"}」；在页面顶部「当前店铺」切换。
              如果浏览器当前登录的是其他店铺，插件会停止采集。
            </p>
            {!activeShop ? (
              <p className="op-hint">
                还没有绑定店铺：去「店铺账号」新增 Temu 账号并点「开始绑定」，登录后会自动读取店铺。
              </p>
            ) : null}
            <div className="op-filter-actions">
              {onOpenAccounts ? (
                <button type="button" className="op-btn op-btn-primary" onClick={onOpenAccounts}>
                  {activeShop ? "切换 / 绑定店铺" : "去绑定店铺"}
                </button>
              ) : null}
            </div>
            {activeShop?.code_status === "local" ? (
              <div className="op-shop-form">
                <span className="op-hint">当前是本地临时码 {activeShop.store_code}，联网后可换成正式店铺码。</span>
                <button
                  type="button"
                  className="op-btn"
                  disabled={shopBusy}
                  onClick={() => void syncShopCode(activeShop.id)}
                >
                  同步店铺码
                </button>
              </div>
            ) : null}
          </div>

          <div className="op-capture-step-head">
            <span className="op-step-label">第 2 步</span>
            <ShopDataGlyph name="cloud" className="op-step-glyph" />
            <strong>开始同步</strong>
          </div>

          <div className="op-capture-actions">
            <button
              type="button"
              className="op-btn op-btn-primary"
              disabled={busy || !selected?.online}
              onClick={() => void startSync("auto")}
            >
              {busy ? "采集中…" : "同步最新数据"}
            </button>
            {busy ? (
              <button
                type="button"
                className="op-btn"
                onClick={() => void stopCapture()}
              >
                终止采集
              </button>
            ) : (
              <button
                type="button"
                className="op-btn"
                onClick={() => void startSync("full")}
              >
                重新全量采集
              </button>
            )}
          </div>
          {!busy ? (
            <p className="op-hint">
              {nextSyncKind === "incremental"
                ? "将执行增量同步：只补最新数据；每 7 天会自动安排一次全量。"
                : "将执行全量同步：依次采集店铺首页、流量、销量、对账、结算和订单。"}
            </p>
          ) : null}

          <details className="op-disclosure">
            <summary>
              <ShopDataGlyph name="key" />
              <span>插件与费率设置</span>
              <small>
                插件{selected?.online ? "在线" : "离线"} · 汇率 {settings.usd_cny_rate} · 佣金 {(settings.commission_rate * 100).toFixed(1)}%
              </small>
            </summary>
            <div className="op-disclosure-body">
              {sessions.length > 1 ? (
                <label className="op-field">
                  <span>插件会话</span>
                  <select value={sessionId} onChange={(event) => setSessionId(event.target.value)}>
                    {sessions.map((session) => (
                      <option key={session.session_id} value={session.session_id}>
                        {session.device_name ? `[${session.device_name}] ` : ""}
                        {session.browser} · v{session.plugin_version || "?"} · {session.online ? "在线" : "离线"}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <p className="op-hint">
                  插件会话：
                  {selected
                    ? `${selected.browser} · v${selected.plugin_version || "?"} · ${selected.online ? "在线" : "离线"}`
                    : "未发现"}
                </p>
              )}

              <p className="op-hint">
                插件配对与设备管理已移到「系统设置 → 插件设置」，这里只负责发起采集和查看进度。
              </p>

              <div className="op-settings">
                <div className="op-settings-head">
                  <strong>费率与汇率</strong>
                  <button type="button" className="op-link" onClick={() => void saveSettings()}>
                    保存
                  </button>
                </div>
                <div className="op-settings-grid">
                  <label className="op-field">
                    <span>USD → CNY</span>
                    <input type="number" min={0} step={0.01} value={rateInput} onChange={(event) => setRateInput(event.target.value)} />
                  </label>
                  <label className="op-field">
                    <span>佣金费率</span>
                    <div className="op-input-suffix">
                      <input type="number" min={0} step={0.1} value={commissionInput} onChange={(event) => setCommissionInput(event.target.value)} />
                      <span>%</span>
                    </div>
                  </label>
                  <label className="op-field">
                    <span>头程单价</span>
                    <div className="op-input-suffix">
                      <input type="number" min={0} step={0.5} value={headInput} onChange={(event) => setHeadInput(event.target.value)} />
                      <span>元/公斤</span>
                    </div>
                  </label>
                </div>
                <p className="op-hint">
                  当前保存值：汇率 {settings.usd_cny_rate} · 佣金 {(settings.commission_rate * 100).toFixed(2)}% · 头程 {settings.head_unit_price_cny} 元/公斤。
                </p>
              </div>
            </div>
          </details>
        </div>

        <div className="op-capture-main">
          <details className="op-disclosure">
            <summary>
              <ShopDataGlyph name="team" />
              <span>团队汇总</span>
              <small>按「店铺 × 日期」汇总，每周六自动提交</small>
            </summary>
            <div className="op-disclosure-body">
            <div className="op-team-intro">
              <p className="op-hint">
                按「店铺 × 日期」汇总本账号全部店铺的数据提交团队服务，每周六自动跑一次。
              </p>
              <ShopDataScene variant="team" className="op-team-scene" />
            </div>
            <div className="op-capture-actions">
              <button
                type="button"
                className="op-btn op-btn-primary"
                disabled={submitBusy}
                onClick={() => void openRollupDialog()}
              >
                {submitBusy ? "读取中…" : "提交本机汇总"}
              </button>
            </div>
            {submitText ? <p className="op-capture-text">{submitText}</p> : null}
            {rollupResult?.stores?.length ? (
              <div className="op-rollup-result">
                <div className="op-rollup-result-head">
                  <strong>本次提交明细</strong>
                  <span>
                    {rollupResult.week_start} · 服务器新增 {rollupResult.created ?? 0} 天 / 更新{" "}
                    {rollupResult.updated ?? 0} 天 / 跳过 {rollupResult.stale ?? 0} 天
                  </span>
                </div>
                {rollupResult.stores.map((store) => (
                  <div className="op-rollup-shop" key={store.store_code || store.name}>
                    <strong>{store.name}</strong>
                    <span>{store.store_code}</span>
                    <em>
                      {store.day_count} 天 / {store.record_count} 条明细
                    </em>
                  </div>
                ))}
                {rollupResult.pending_local_shops?.length ? (
                  <p className="op-hint">
                    未提交：
                    {rollupResult.pending_local_shops.map((shop) => shop.name).join("、")}
                  </p>
                ) : null}
              </div>
            ) : null}
            </div>
          </details>

          <div className="op-card op-capture-result">
            <div className="op-panel-head">
              <div className="op-step-copy">
                <span className="op-step-label">第 3 步</span>
                <ShopDataGlyph name="check" className="op-step-glyph" />
                <strong>本次采集</strong>
              </div>
              <span className={`op-chip ${statusChip.tone}`}>
                {statusChip.label}
              </span>
            </div>
            {status?.progress ? (
              <div className="op-progress">
                <div className="op-progress-head">
                  <span>
                    {progressLabel}
                    {progressStepText}
                  </span>
                  <strong>{progressPercent}%</strong>
                </div>
                <div className="op-progress-track">
                  <div className="op-progress-bar" style={{ width: `${progressPercent}%` }} />
                </div>
                {status.progress.detail ? <small>{status.progress.detail}</small> : null}
              </div>
            ) : null}
            {!status ? (
              <EmptyBlock
                art="capture"
                title="还没有采集任务"
                desc="点左侧「同步最新数据」开始；系统会自动选择增量或全量，并在下方逐步显示进度。"
              />
            ) : (
              <div className="op-capture-body">
                {status.command_type === "temu_capture_all" || status.command_type === "temu_capture_incremental" ? (
                  <div className="op-capture-meta">
                    <span>
                      {status.status === "queued" || status.status === "sent"
                        ? "等待插件接单…"
                        : `已完成 ${
                            stepRows.filter((item) => ["ok", "skipped", "failed"].includes(item.state)).length
                          } / ${Math.max(stepRows.length, status.progress?.step_total || 0)} 步`}
                    </span>
                    {status.status_text ? <em>{status.status_text}</em> : null}
                  </div>
                ) : (
                  <div className="op-capture-summary">
                    <div><span>采集模式</span><strong>{status.capture_mode === "order_detail" ? "订单详情" : "订单列表"}</strong></div>
                    <div><span>父订单</span><strong>{numberText(status.order_count)}</strong></div>
                    <div><span>子订单</span><strong>{numberText(status.sub_order_count)}</strong></div>
                    <div><span>GMV</span><strong>{money(status.gmv_total)}</strong></div>
                    <div><span>申报价合计</span><strong>{money(status.declared_total)}</strong></div>
                    <div><span>尾程预估</span><strong>{status.tail_currency === "USD" ? money(status.tail_freight_total, "USD") : money(status.tail_freight_total)}</strong></div>
                    {status.status_text ? <p className="op-capture-text">{status.status_text}</p> : null}
                  </div>
                )}

                {resultSummary.total > 0 ? (
                  <div className="op-run-foot">
                    <p className="op-capture-text">
                      成功 {resultSummary.ok} · 跳过 {resultSummary.skipped} · 失败 {resultSummary.failed}
                      {resultSummary.running ? ` · 进行中 ${resultSummary.running}` : ""}
                    </p>
                    {failedSteps.length && !busy ? (
                      <button
                        type="button"
                        className="op-btn op-btn-sm"
                        onClick={() => void retryFailedSteps({
                          results: status.results || [],
                          shop_name: activeShop?.name || "",
                        })}
                      >
                        补采失败项（{failedSteps.length}）
                      </button>
                    ) : null}
                  </div>
                ) : null}

                {stepRows.length ? (
                  <div className="op-steps-grid">
                    {stepRows.map((item) => {
                      const retryable = ["skipped", "failed"].includes(item.state)
                        && RETRYABLE_CAPTURE_STEPS.has(item.key)
                        && !busy;
                      return (
                        <article key={item.key} className={`op-step-card is-${item.state || "running"}`}>
                          <header>
                            <span>{item.label || item.key}</span>
                            <em>{CAPTURE_STEP_STATE[item.state] || item.state || "进行中"}</em>
                          </header>
                          <p>{item.detail || "—"}</p>
                          {retryable ? (
                            <button
                              type="button"
                              className="op-btn op-btn-sm"
                              onClick={() => void retryStep(item.key, item.label || item.key)}
                            >
                              重试这一步
                            </button>
                          ) : null}
                        </article>
                      );
                    })}
                  </div>
                ) : null}

                {status.error ? <p className="op-alert is-error">{status.error}</p> : null}
                {status.help ? <p className="op-hint">{status.help}</p> : null}
              </div>
            )}
          </div>

          <CaptureCenterCard
            runs={recentRuns}
            loading={recentLoading}
            busy={busy}
            onRetry={(run) => void retryFailedSteps(run)}
            onRefresh={() => void refreshRecent()}
          />

        </div>
      </div>

      <TeamRollupDialog
        open={rollupPreview !== null}
        preview={rollupPreview}
        busy={submitBusy}
        onCancel={() => setRollupPreview(null)}
        onConfirm={() => void submitTeamRollup(rollupPreview?.week_start || "")}
      />
    </section>
  );
}

const CAPTURE_STEP_STATE: Record<string, string> = {
  running: "采集中",
  ok: "完成",
  skipped: "跳过",
  failed: "失败",
};

function formatDay(timestamp: number): string {
  if (!timestamp) return "—";
  return new Date(timestamp).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

