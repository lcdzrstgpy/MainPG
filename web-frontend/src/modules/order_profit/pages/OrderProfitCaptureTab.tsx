import { useEffect, useRef, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import { getAuthAccount } from "../../../transport/http/client";
import { orderProfitApi } from "../api/orderProfitApi";
import { ShopConfirmDialog } from "../components/ShopConfirmDialog";
import { money, numberText } from "../format";
import type {
  OrderProfitCaptureStatus,
  OrderProfitSettings,
  PluginSession,
  ProfitShop,
} from "../types";

export function OrderProfitCaptureTab({
  isActive,
  onCaptured,
  onOpenAccounts,
  refreshToken = 0,
  onShopChanged,
}: {
  isActive: boolean;
  onCaptured: () => void;
  onOpenAccounts?: () => void;
  /** 页头切换店铺后自增，用来重新拉取当前采集店铺。 */
  refreshToken?: number;
  /** 采集页自己切店铺时，把页头下拉同步过去。 */
  onShopChanged?: (name: string) => void;
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
  const [shops, setShops] = useState<ProfitShop[]>([]);
  const [activeShop, setActiveShop] = useState<ProfitShop | null>(null);
  const [newShopName, setNewShopName] = useState("");
  const [newShopNote, setNewShopNote] = useState("");
  const [shopBusy, setShopBusy] = useState(false);
  const [staffNoNeeded, setStaffNoNeeded] = useState(false);
  const [staffNoInput, setStaffNoInput] = useState("");
  const [confirmState, setConfirmState] = useState<{
    label: string;
    run: () => Promise<void>;
  } | null>(null);
  const [submitBusy, setSubmitBusy] = useState(false);
  const [submitText, setSubmitText] = useState("");
  const pollTimer = useRef<number | null>(null);
  // 记住当前指令号：刚下发时 status 还没回来，终止按钮也要能停。
  const activeCommandIdRef = useRef(0);
  const account = getAuthAccount<{ username?: string }>() ?? {};

  /** 采集前统一走一次店铺确认，避免把数据录到别的店。 */
  const requestRun = (label: string, run: () => Promise<void>) => {
    if (busy) return;
    if (!activeShop) {
      showToast("请先绑定并选中目标店铺，再开始采集", "error");
      return;
    }
    setConfirmState({ label, run });
  };

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
        setShops(shopPayload.shops || []);
        setActiveShop(shopPayload.active || null);
        setSettings(settingsPayload.settings);
        setRateInput(String(settingsPayload.settings.usd_cny_rate));
        setCommissionInput(String(Number(settingsPayload.settings.commission_rate) * 100));
        setHeadInput(String(settingsPayload.settings.head_unit_price_cny));
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

  const pollStatus = async (commandId: number) => {
    try {
      const next = await orderProfitApi.captureStatus(commandId);
      setStatus(next);
      if (next.status === "succeeded") {
        setBusy(false);
        if (next.command_type === "temu_capture_all") {
          showToast(next.status_text || "一键采集完成", next.ok ? "success" : "error");
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
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    }
  };

  const startAll = async (incremental = false) => {
    if (busy) return;
    const activeSession = await resolveSessionId();
    if (!activeSession) {
      showToast("请先在插件弹窗点「连接插件」，并保持 Temu 页面打开", "error");
      return;
    }
    clearPoll();
    setBusy(true);
    setStatus(null);
    setError("");
    try {
      const command = await orderProfitApi.startCaptureAll(activeSession, incremental);
      activeCommandIdRef.current = command.command_id;
      showToast(
        incremental
          ? "已下发增量更新：只补最新数据（订单/流水只读第一页，详情补最近 20 单）"
          : "已下发一键全量采集：插件会自动切页采集并直接入库",
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

  const bindShop = async () => {
    const name = newShopName.trim();
    if (!name) {
      showToast("请输入店铺名称", "error");
      return;
    }
    setShopBusy(true);
    try {
      const payload = await orderProfitApi.bindShop({
        name,
        note: newShopNote.trim(),
      });
      setShops(payload.shops || []);
      setActiveShop(payload.shop || payload.active || null);
      setNewShopName("");
      setNewShopNote("");
      const backfilled = Number(payload.backfilled || 0);
      const code = payload.shop?.store_code || "";
      showToast(
        payload.sync_warning
          ? `${payload.sync_warning}（店铺码：${code}）`
          : backfilled
            ? `已绑定店铺「${name}」（${code}），并把 ${backfilled} 行未归类历史数据归入该店铺`
            : `已绑定店铺「${name}」（${code}），采集结果会归到这个店铺`,
        payload.sync_warning ? "info" : "success",
      );
      onCaptured();
    } catch (cause) {
      // 团队服务还没拿到员工标识：先让用户填一次，再自动重试绑定。
      if (String(cause instanceof Error ? cause.message : cause).includes("staff_no_required")) {
        setStaffNoNeeded(true);
        showToast("首次创建店铺需要先填写员工标识，填好后再点一次绑定", "info");
        return;
      }
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setShopBusy(false);
    }
  };

  const saveStaffNo = async () => {
    const value = staffNoInput.trim();
    if (!value) {
      showToast("请填写员工标识（用于辨识和后续提交）", "error");
      return;
    }
    setShopBusy(true);
    try {
      await orderProfitApi.saveTeamProfile(value);
      setStaffNoNeeded(false);
      setStaffNoInput("");
      showToast("员工标识已保存，正在重新绑定店铺", "success");
      await bindShop();
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setShopBusy(false);
    }
  };

  const syncShopCode = async (shopId: string) => {
    if (!shopId) return;
    setShopBusy(true);
    try {
      const payload = await orderProfitApi.syncShopCode(shopId);
      setShops(payload.shops || []);
      setActiveShop(payload.shop || null);
      showToast(`店铺码已同步为 ${payload.shop?.store_code || ""}`, "success");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setShopBusy(false);
    }
  };

  const submitTeamRollup = async () => {
    setSubmitBusy(true);
    try {
      const payload = await orderProfitApi.submitTeamRollup();
      const pending = payload.pending_local_shops?.length || 0;
      const text = payload.ok
        ? `已提交 ${payload.week_start} 的本周汇总：新增 ${payload.created ?? 0} 天、更新 ${payload.updated ?? 0} 天、跳过 ${payload.stale ?? 0} 天`
          + (pending ? `；还有 ${pending} 家店铺是本地临时码，未提交` : "")
        : payload.status_text || "没有可提交的数据";
      setSubmitText(text);
      showToast(text, payload.ok ? "success" : "info");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setSubmitBusy(false);
    }
  };

  const cleanupUnboundShops = async () => {
    if (!window.confirm("清理所有未与「店铺账号」绑定的店铺？\n只从店铺下拉里移除，已采集的订单数据不会删除。")) {
      return;
    }
    setShopBusy(true);
    try {
      const result = await orderProfitApi.cleanupShops();
      const payload = await orderProfitApi.listShops();
      setShops(payload.shops || []);
      setActiveShop(payload.active || null);
      showToast(
        result.deleted ? `已清理 ${result.deleted} 个未绑定店铺` : "没有需要清理的店铺",
        result.deleted ? "success" : "info",
      );
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setShopBusy(false);
    }
  };

  const switchShop = async (shopId: string) => {
    if (!shopId) return;
    setShopBusy(true);
    try {
      const payload = await orderProfitApi.activateShop(shopId);
      setShops(payload.shops || []);
      setActiveShop(payload.shop || null);
      if (payload.shop?.name) onShopChanged?.(payload.shop.name);
      const backfilled = Number(payload.backfilled || 0);
      showToast(
        `当前采集店铺已切换为「${payload.shop?.name || ""}」`
          + (backfilled ? `，${backfilled} 行未归类数据已归入该店铺` : ""),
        "success",
      );
      onCaptured();
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setShopBusy(false);
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
            <strong>采集设置</strong>
            <span className={`op-chip ${selected?.online ? "is-ok" : "is-warn"}`}>
              {selected?.online ? "插件在线" : "插件离线"}
            </span>
          </div>

          <div className="op-shop-block">
            <div className="op-panel-head">
              <strong>店铺绑定</strong>
              <span className={`op-chip ${activeShop ? "is-ok" : "is-warn"}`}>
                {activeShop
                  ? `当前：${activeShop.name}${activeShop.store_code ? ` · ${activeShop.store_code}` : ""}`
                  : "未绑定"}
              </span>
            </div>
            <label className="op-field">
              <span>已绑定店铺</span>
              <select
                value={activeShop?.id || ""}
                disabled={shopBusy || !shops.length}
                onChange={(event) => void switchShop(event.target.value)}
              >
                {!shops.length ? <option value="">还没有绑定店铺</option> : null}
                {shops.map((shop) => (
                  <option key={shop.id} value={shop.id}>
                    {shop.name}
                    {shop.store_code ? ` · ${shop.store_code}` : ""}
                    {shop.is_active ? "（当前采集）" : ""}
                  </option>
                ))}
              </select>
            </label>
            <div className="op-shop-form">
              <span className="op-hint">店铺来自「店铺账号」登录后的自动读取。</span>
              <div className="op-filter-actions">
                {onOpenAccounts ? (
                  <button type="button" className="op-btn op-btn-primary" onClick={onOpenAccounts}>
                    去店铺账号绑定
                  </button>
                ) : null}
                <button type="button" className="op-btn" disabled={shopBusy} onClick={() => void cleanupUnboundShops()}>
                  清理未绑定店铺
                </button>
              </div>
            </div>
            {staffNoNeeded ? (
              <div className="op-shop-form">
                <label className="op-field">
                  <span>员工标识（首次必填）</span>
                  <input
                    type="text"
                    value={staffNoInput}
                    placeholder="例如 张三 / zhang-san"
                    onChange={(event) => setStaffNoInput(event.target.value)}
                  />
                </label>
                <button
                  type="button"
                  className="op-btn op-btn-primary"
                  disabled={shopBusy}
                  onClick={() => void saveStaffNo()}
                >
                  {shopBusy ? "保存中…" : "保存标识并绑定"}
                </button>
              </div>
            ) : null}
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

          <p className="op-hint">
            插件配对与设备管理已移到「系统设置 → 插件设置」，这里只负责发起采集和查看进度。
          </p>

          <label className="op-field">
            <span>插件会话</span>
            <select value={sessionId} onChange={(event) => setSessionId(event.target.value)}>
              {!sessions.length ? <option value="">未发现插件会话</option> : null}
              {sessions.map((session) => (
                <option key={session.session_id} value={session.session_id}>
                  {session.device_name ? `[${session.device_name}] ` : ""}
                  {session.browser} · v{session.plugin_version || "?"} · {session.online ? "在线" : "离线"}
                </option>
              ))}
            </select>
          </label>

          <div className="op-capture-actions">
            <button
              type="button"
              className="op-btn op-btn-primary"
              disabled={busy || !selected?.online}
              onClick={() => requestRun("一键全量采集", () => startAll(false))}
            >
              {busy ? "采集中…" : "一键全量采集"}
            </button>
            <button
              type="button"
              className="op-btn"
              disabled={busy || !selected?.online}
              onClick={() => requestRun("增量更新", () => startAll(true))}
            >
              增量更新
            </button>
            <button
              type="button"
              className="op-btn"
              disabled={!busy}
              onClick={() => void stopCapture()}
            >
              终止采集
            </button>
          </div>

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

        <div className="op-capture-main">
          <div className="op-card op-capture-result">
            <div className="op-panel-head">
              <strong>团队汇总</strong>
              <span className="op-chip">每周六自动提交</span>
            </div>
            <p className="op-hint">
              按「店铺 × 日期」汇总本机数据提交团队服务，每周六自动跑一次。
            </p>
            <div className="op-capture-actions">
              <button
                type="button"
                className="op-btn op-btn-primary"
                disabled={submitBusy}
                onClick={() => void submitTeamRollup()}
              >
                {submitBusy ? "提交中…" : "提交本周汇总"}
              </button>
            </div>
            {submitText ? <p className="op-capture-text">{submitText}</p> : null}
          </div>

          <div className="op-card op-capture-result">
            <div className="op-panel-head">
              <strong>本次采集</strong>
              <span className={`op-chip ${status?.status === "succeeded" && status.ok ? "is-ok" : status?.status === "failed" ? "is-error" : ""}`}>
                {status ? statusLabel(status.status) : "未开始"}
              </span>
            </div>
            {status?.progress ? (
              <div className="op-progress">
                <div className="op-progress-head">
                  <span>
                    {status.progress.step_label || "采集中"}
                    {status.progress.step_total
                      ? `（${status.progress.step}/${status.progress.step_total}）`
                      : ""}
                  </span>
                  <strong>{status.progress.percent}%</strong>
                </div>
                <div className="op-progress-track">
                  <div className="op-progress-bar" style={{ width: `${status.progress.percent}%` }} />
                </div>
                {status.progress.detail ? <small>{status.progress.detail}</small> : null}
              </div>
            ) : null}
            {!status ? (
              <div className="op-empty op-empty-compact">
                <strong>还没有采集任务</strong>
                <span>点「一键全量采集」开始。</span>
              </div>
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

                {stepRows.length ? (
                  <div className="op-steps-grid">
                    {stepRows.map((item) => {
                      const retryable = ["skipped", "failed"].includes(item.state)
                        && RETRYABLE_STEPS.has(item.key)
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

        </div>
      </div>

      <ShopConfirmDialog
        open={confirmState !== null}
        actionLabel={confirmState?.label || ""}
        shop={activeShop}
        username={account.username || ""}
        onCancel={() => setConfirmState(null)}
        onConfirm={() => {
          const pending = confirmState;
          setConfirmState(null);
          if (pending) void pending.run();
        }}
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

/** 这些步骤能单独重采（对应 /captures/resource 的资源名）。 */
const RETRYABLE_STEPS = new Set(["store", "traffic", "sales", "finance", "settlement", "orders"]);

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    queued: "排队中",
    sent: "已下发",
    running: "采集中",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已终止",
  };
  return map[status] || status || "未开始";
}
