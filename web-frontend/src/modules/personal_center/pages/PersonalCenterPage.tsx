import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { clearAuthSession, getAuthAccount } from "../../../transport/http/client";
import {
  changeAccountPassword,
  createTopupOrder,
  loadBillingSummary,
  loadBillingUsageHistory,
  type BillingPackage,
  type BillingSummary,
  type BillingUsageEntry,
  type TopupOrderResponse,
} from "../api/personalCenterApi";
import "../styles/personalCenter.css";

type AccountSnapshot = {
  account_id?: string;
  username?: string;
  email?: string;
  role?: string;
  workspace_code?: string;
  workspace_name?: string;
};

const providerMeta = {
  wechat: { label: "微信支付", icon: "iconfont icon-wechat-fill", className: "is-wechat" },
  alipay: { label: "支付宝", icon: "iconfont icon-alipay-circle-fill", className: "is-alipay" },
} as const;

function money(amountCents: number) {
  return `¥${(amountCents / 100).toFixed(2)}`;
}

/** 服务端返回的计费流水时间为 UTC（如 2026-08-21T14:17:12+00:00），
 *  这里转换为浏览器本地时区显示，避免直接截断显示成 UTC 时间。 */
function formatUsageTime(iso: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso.replace("T", " ").slice(0, 19);
  }
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  );
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "待支付",
    paid: "已入账",
    closed: "已关闭",
    failed: "失败",
    refunded: "已退款",
  };
  return labels[status] ?? status;
}

const pricingFeatures: Array<{ key: string; label: string; note: string }> = [
  { key: "product_processing.image_grid_2k", label: "智能生图", note: "商品图片生成" },
  { key: "product_processing.text", label: "商品文本", note: "标题 / 卖点 / 详情文案" },
  { key: "product_processing.batch", label: "批量链接处理", note: "整批商品处理任务" },
];

function usageServiceLabel(featureKey: string) {
  if (featureKey === "pod_customization.batch") return "POD 定制";
  if (featureKey === "product_processing.image_grid_2k") return "智能生图";
  if (featureKey === "product_processing.batch") return "批量链接处理";
  return "商品文本";
}

// 用量明细的调用信息：内部模型名（doubao-* 等供应商标识）不对外展示。
function usageDetailText(entry: BillingUsageEntry): string | null {
  const raw = entry.model || entry.provider || "";
  if (!raw) return "等待上游";
  if (/doubao/i.test(raw)) return null;
  return raw;
}

/** 积分/钱包概要本地缓存：冷却窗口内页面刷新直接复用缓存，避免每次进入都请求服务器。 */
const BALANCE_CACHE_PREFIX = "mainpg.billing.summary.cache.v1";

type BalanceCachePayload = { summary: BillingSummary; fetchedAt: number };

function balanceCacheKey(accountId?: string) {
  return `${BALANCE_CACHE_PREFIX}.${accountId || "anonymous"}`;
}

function readBalanceCache(key: string): BalanceCachePayload | null {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) ?? "null") as BalanceCachePayload | null;
    if (!parsed || typeof parsed.fetchedAt !== "number" || !parsed.summary) return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeBalanceCache(key: string, summary: BillingSummary) {
  try {
    window.localStorage.setItem(key, JSON.stringify({ summary, fetchedAt: Date.now() }));
  } catch {
    // localStorage 不可用（隐私模式等）时静默忽略，退化为每次请求
  }
}

/** 消费流水本地缓存：与积分概要同理，按账号隔离，冷却窗口内同条件复用。 */
const USAGE_CACHE_PREFIX = "mainpg.billing.usage.cache.v1";

type UsageCachePayload = { items: BillingUsageEntry[]; filterKey: string; fetchedAt: number };

function usageCacheKey(accountId?: string) {
  return `${USAGE_CACHE_PREFIX}.${accountId || "anonymous"}`;
}

function buildUsageFilterKey(feature: string, status: string, from: string, to: string) {
  return `${feature}|${status}|${from}|${to}`;
}

function readUsageCache(key: string): UsageCachePayload | null {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) ?? "null") as UsageCachePayload | null;
    if (!parsed || typeof parsed.fetchedAt !== "number" || !Array.isArray(parsed.items)) return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeUsageCache(key: string, payload: UsageCachePayload) {
  try {
    window.localStorage.setItem(key, JSON.stringify(payload));
  } catch {
    // localStorage 不可用（隐私模式等）时静默忽略，退化为每次请求
  }
}

export function PersonalCenterPage() {
  const account = getAuthAccount<AccountSnapshot>();
  // 积分/钱包概要本地缓存：冷却窗口内页面刷新直接复用缓存，先展示、不阻塞。
  const balanceCacheKeyValue = balanceCacheKey(account?.account_id);
  const cachedBalance = readBalanceCache(balanceCacheKeyValue);
  // 消费流水本地缓存：与概要缓存同理，按账号隔离、跨页面刷新复用。
  const usageCacheKeyValue = usageCacheKey(account?.account_id);
  const cachedUsage = readUsageCache(usageCacheKeyValue);
  const defaultUsageFilterKey = buildUsageFilterKey("", "", "", "");

  const [summary, setSummary] = useState<BillingSummary | null>(cachedBalance?.summary ?? null);
  const [activePanel, setActivePanel] = useState<"wallet" | "usage" | "pricing">("wallet");
  const [usageEntries, setUsageEntries] = useState<BillingUsageEntry[]>(
    cachedUsage && cachedUsage.filterKey === defaultUsageFilterKey ? cachedUsage.items : [],
  );
  const [usageLoading, setUsageLoading] = useState(false);
  const [usageError, setUsageError] = useState("");
  const [loading, setLoading] = useState(!cachedBalance?.summary);
  const [error, setError] = useState("");
  const [selectedPackage, setSelectedPackage] = useState("");
  const [customAmount, setCustomAmount] = useState("");
  const [creating, setCreating] = useState(false);
  const [createdOrder, setCreatedOrder] = useState<TopupOrderResponse | null>(null);
  const [paymentNotice, setPaymentNotice] = useState("");
  const [pendingPaymentOrderId, setPendingPaymentOrderId] = useState("");
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [passwordSuccess, setPasswordSuccess] = useState("");
  const [passwordError, setPasswordError] = useState("");
  // 消费流水刷新保护：30 秒内（含页面刷新，随缓存持久化）相同筛选条件不重复请求；筛选变更因缓存键变化自动重新拉取。
  const USAGE_REFRESH_COOLDOWN_MS = 30_000;
  // 消费流水筛选条件（服务/状态/日期）。
  const [filterFeature, setFilterFeature] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterDateFrom, setFilterDateFrom] = useState("");
  const [filterDateTo, setFilterDateTo] = useState("");

  const loadUsage = useCallback((force = false) => {
    const filterKey = buildUsageFilterKey(filterFeature, filterStatus, filterDateFrom, filterDateTo);
    const cached = readUsageCache(usageCacheKeyValue);
    // 冷却窗口内同条件已有缓存：直接复用，不再请求服务器（页面刷新后依然有效）。
    if (!force && cached && cached.filterKey === filterKey && Date.now() - cached.fetchedAt < USAGE_REFRESH_COOLDOWN_MS) {
      setUsageEntries(cached.items);
      setUsageError("");
      return;
    }
    setUsageLoading(true);
    setUsageError("");
    loadBillingUsageHistory({
      featureKey: filterFeature || undefined,
      usageStatus: filterStatus || undefined,
      dateFrom: filterDateFrom || undefined,
      dateTo: filterDateTo || undefined,
    })
      .then((payload) => {
        setUsageEntries(payload.items);
        writeUsageCache(usageCacheKeyValue, { items: payload.items, filterKey, fetchedAt: Date.now() });
      })
      .catch((exc) => setUsageError(exc instanceof Error ? exc.message : "读取消费流水失败"))
      .finally(() => setUsageLoading(false));
  }, [filterFeature, filterStatus, filterDateFrom, filterDateTo, usageCacheKeyValue]);

  const hasUsageFilter = Boolean(filterFeature || filterStatus || filterDateFrom || filterDateTo);
  const resetUsageFilters = () => {
    setFilterFeature("");
    setFilterStatus("");
    setFilterDateFrom("");
    setFilterDateTo("");
    // 筛选变更后由下方 effect 依据新的筛选键自动重新拉取。
  };

  const customAmountCents = useMemo(() => {
    if (!/^\d+$/.test(customAmount)) return 0;
    const yuan = Number(customAmount);
    return Number.isSafeInteger(yuan) && yuan >= 1 && yuan <= 3000 ? yuan * 100 : 0;
  }, [customAmount]);

  const activePackage = useMemo(() => {
    if (selectedPackage === "custom" && customAmountCents) {
      const pointsPerCny = summary?.pricing.points_per_cny ?? 100;
      return {
        package_id: "custom",
        label: "自定义积分充值",
        amount_cents: customAmountCents,
        points: (customAmountCents / 100) * pointsPerCny,
      } satisfies BillingPackage;
    }
    return summary?.topup_products.find((item) => item.package_id === selectedPackage) ?? summary?.topup_products[0];
  }, [customAmountCents, selectedPackage, summary]);

  const refresh = useCallback(() => {
    setLoading(true);
    setError("");
    loadBillingSummary()
      .then((payload) => {
        setSummary(payload);
        writeBalanceCache(balanceCacheKeyValue, payload);
        lastBalanceRefreshAt.current = Date.now();
        setSelectedPackage((current) => current || payload.topup_products[0]?.package_id || "");
      })
      .catch((exc) => setError(exc instanceof Error ? exc.message : "读取个人中心失败"))
      .finally(() => setLoading(false));
  }, [balanceCacheKeyValue]);

  // 可用积分刷新冷却：30 秒内（含页面刷新，时间戳随缓存持久化到本地）不重复请求；
  // 已读取的概要缓存到 localStorage，刷新页面时先展示缓存，新鲜则不再请求服务器。
  const BALANCE_REFRESH_COOLDOWN_MS = 30_000;
  const lastBalanceRefreshAt = useRef(cachedBalance?.fetchedAt ?? 0);
  const [balanceCooldownSeconds, setBalanceCooldownSeconds] = useState(0);
  const balanceCooldownActive = balanceCooldownSeconds > 0;

  const refreshBalance = useCallback((force = false) => {
    if (!force && Date.now() - lastBalanceRefreshAt.current < BALANCE_REFRESH_COOLDOWN_MS) {
      return;
    }
    lastBalanceRefreshAt.current = Date.now();
    setBalanceCooldownSeconds(BALANCE_REFRESH_COOLDOWN_MS / 1000);
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!balanceCooldownActive) return;
    const timer = window.setInterval(
      () => setBalanceCooldownSeconds((seconds) => Math.max(0, seconds - 1)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [balanceCooldownActive]);

  useEffect(() => {
    // 冷却窗口内已有新鲜缓存：直接复用，不再请求服务器。
    if (Date.now() - lastBalanceRefreshAt.current < BALANCE_REFRESH_COOLDOWN_MS) {
      setLoading(false);
      return;
    }
    refreshBalance(true);
  }, [refreshBalance]);

  useEffect(() => {
    if (activePanel !== "usage") return;
    loadUsage(false);
  }, [activePanel, loadUsage]);

  useEffect(() => {
    if (!pendingPaymentOrderId) return;

    let disposed = false;
    const refreshPaymentStatus = () => {
      void loadBillingSummary()
        .then((payload) => {
          if (disposed) return;
          setSummary(payload);
          const order = payload.recent_orders.find((item) => item.order_id === pendingPaymentOrderId);
          if (order?.status === "paid") {
            setPendingPaymentOrderId("");
            setPaymentNotice(`充值成功，${order.points.toLocaleString()} 积分已到账。`);
          }
        })
        .catch(() => {
          // The regular refresh action remains available if the network is briefly unavailable.
        });
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") refreshPaymentStatus();
    };

    refreshPaymentStatus();
    const timer = window.setInterval(refreshPaymentStatus, 4000);
    window.addEventListener("focus", refreshPaymentStatus);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshPaymentStatus);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [pendingPaymentOrderId]);

  useEffect(() => {
    if (!passwordOpen) return;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !passwordBusy) setPasswordOpen(false);
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [passwordBusy, passwordOpen]);

  const openPasswordDialog = () => {
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setPasswordError("");
    setPasswordSuccess("");
    setPasswordOpen(true);
  };

  const closePasswordDialog = () => {
    if (!passwordBusy) setPasswordOpen(false);
  };

  const submitPasswordChange = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setPasswordError("");
    setPasswordSuccess("");
    if (newPassword.length < 6) {
      setPasswordError("新密码至少需要 6 个字符");
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordError("两次输入的新密码不一致");
      return;
    }
    if (currentPassword === newPassword) {
      setPasswordError("新密码不能与当前密码相同");
      return;
    }

    setPasswordBusy(true);
    try {
      await changeAccountPassword({
        account_id: summary?.account.account_id || account?.account_id,
        username: account?.username || summary?.account.username,
        email: account?.email,
        current_password: currentPassword,
        new_password: newPassword,
      });
      setPasswordSuccess("密码修改成功，即将退出并返回登录页…");
      clearAuthSession();
      window.setTimeout(() => window.location.reload(), 900);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : "修改密码失败";
      setPasswordError(message.includes("invalid username/email or password") ? "当前密码不正确" : message);
    } finally {
      setPasswordBusy(false);
    }
  };

  const submitTopup = async (product?: BillingPackage) => {
    if (!product) return;
    setCreating(true);
    setError("");
    setPaymentNotice("");
    setCreatedOrder(null);
    try {
      const response = await createTopupOrder({
        provider: "alipay",
        package_id: product.package_id,
        ...(product.package_id === "custom" ? { amount_cents: product.amount_cents } : {}),
      });
      setCreatedOrder(response);
      const payload = await loadBillingSummary();
      setSummary(payload);
      writeBalanceCache(balanceCacheKeyValue, payload);
      lastBalanceRefreshAt.current = Date.now();
      setPendingPaymentOrderId(response.order.order_id);

      if (response.payment.mode === "page_pay" && response.payment.pay_url) {
        setPaymentNotice("正在跳转支付宝付款页面，付款完成后返回此页会自动刷新积分。");
        window.location.assign(response.payment.pay_url);
        return;
      }
      setPaymentNotice(response.payment.message || "支付宝付款暂不可用，请稍后重试。");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "创建充值订单失败");
    } finally {
      setCreating(false);
    }
  };

  return (
    <section className="personal-center-page">
      <div className="personal-hero">
        <div className="personal-avatar-card">
          <div className="personal-avatar">
            {(account?.username || summary?.account.username || "U").slice(0, 1).toUpperCase()}
          </div>
          <div>
            <p>个人中心</p>
            <h1>{account?.username || summary?.account.username || "当前用户"}</h1>
            <span>{account?.workspace_name || account?.workspace_code || summary?.account.workspace_code || "默认工作区"}</span>
          </div>
        </div>
        <div className="personal-hero-actions">
          <div className="personal-security-pill">
            <span className="iconfont icon-lock-fill" aria-hidden="true" />
            <strong>服务器账本校验</strong>
            <span>本地数据不作为余额依据</span>
          </div>
          <button className="personal-password-entry" type="button" onClick={openPasswordDialog}>
            <span className="iconfont icon-key" aria-hidden="true" />
            修改密码
          </button>
        </div>
      </div>

      {passwordOpen && (
        <div className="personal-password-layer" onMouseDown={closePasswordDialog}>
          <section
            className="personal-password-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="personal-password-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header>
              <div>
                <span>ACCOUNT SECURITY</span>
                <h2 id="personal-password-title">修改登录密码</h2>
                <p>验证当前密码后设置新密码，修改成功会退出当前登录。</p>
              </div>
              <button type="button" onClick={closePasswordDialog} disabled={passwordBusy} aria-label="关闭">×</button>
            </header>
            <form onSubmit={(event) => void submitPasswordChange(event)}>
              <label>
                <span>当前密码</span>
                <input
                  autoFocus
                  type="password"
                  autoComplete="current-password"
                  value={currentPassword}
                  onChange={(event) => setCurrentPassword(event.target.value)}
                  placeholder="请输入当前登录密码"
                  required
                />
              </label>
              <label>
                <span>新密码</span>
                <input
                  type="password"
                  autoComplete="new-password"
                  value={newPassword}
                  onChange={(event) => setNewPassword(event.target.value)}
                  placeholder="至少 6 个字符"
                  minLength={6}
                  required
                />
              </label>
              <label>
                <span>确认新密码</span>
                <input
                  type="password"
                  autoComplete="new-password"
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  placeholder="请再次输入新密码"
                  minLength={6}
                  required
                />
              </label>
              {passwordError && <p className="personal-password-message is-error">{passwordError}</p>}
              {passwordSuccess && <p className="personal-password-message is-success">{passwordSuccess}</p>}
              <footer>
                <button type="button" onClick={closePasswordDialog} disabled={passwordBusy || Boolean(passwordSuccess)}>取消</button>
                <button className="is-primary" type="submit" disabled={passwordBusy || Boolean(passwordSuccess)}>
                  {passwordBusy ? "正在修改…" : "确认修改"}
                </button>
              </footer>
            </form>
          </section>
        </div>
      )}

      {error && <div className="personal-alert is-error">{error}</div>}
      {loading && <div className="personal-alert">正在读取服务器账户与积分数据...</div>}

      <div className="personal-stats">
        <div className="personal-stat is-balance">
          <span>可用积分</span>
          <b>{summary?.wallet.available_points.toLocaleString() ?? "--"}</b>
          {loading && <span className="personal-stat-spinner" aria-label="积分刷新中" />}
          <button
            type="button"
            className="personal-stats-refresh is-on-dark"
            onClick={() => refreshBalance()}
            disabled={balanceCooldownActive || loading}
            aria-label="刷新可用积分"
          >
            {balanceCooldownActive
              ? `${balanceCooldownSeconds} 秒后可刷新`
              : loading
                ? <><span className="personal-spinner" aria-hidden="true" />刷新中…</>
                : "↻ 刷新"}
          </button>
        </div>
        <div className="personal-stat">
          <span>总积分</span>
          <b>{summary?.wallet.points_balance.toLocaleString() ?? "--"}</b>
        </div>
        <div className="personal-stat">
          <span>冻结积分</span>
          <b>{summary?.wallet.frozen_points.toLocaleString() ?? "--"}</b>
        </div>
        <div className="personal-stat">
          <span>换算比例</span>
          <b>{summary?.pricing.ratio_label ?? "1 元 = 100 积分"}</b>
          <button type="button" className="personal-stats-refresh" onClick={() => refreshBalance()} disabled={balanceCooldownActive || loading} aria-label="刷新余额">
            {balanceCooldownActive ? `${balanceCooldownSeconds} 秒后` : loading ? <><span className="personal-spinner" aria-hidden="true" />刷新中…</> : "↻ 刷新"}
          </button>
        </div>
      </div>

      <div className="personal-content-layout">
        <aside className="personal-subnav" aria-label="个人中心二级导航">
          <button type="button" className={activePanel === "wallet" ? "is-active" : ""} onClick={() => setActivePanel("wallet")}>
            <span className="iconfont icon-wallet-fill" aria-hidden="true" /> 积分钱包
          </button>
          <button type="button" className={activePanel === "usage" ? "is-active" : ""} onClick={() => setActivePanel("usage")}>
            <span className="iconfont icon-accountbook-fill" aria-hidden="true" /> 消费流水
          </button>
          <button type="button" className={activePanel === "pricing" ? "is-active" : ""} onClick={() => setActivePanel("pricing")}>
            <span className="iconfont icon-calculator" aria-hidden="true" /> 计费规则
          </button>
          <p>余额、费率与消费记录均由服务器账本实时校验。</p>
        </aside>

        <div className="personal-panel-content">
        {activePanel === "wallet" ? <div className="personal-grid">
        <article className="personal-card topup-card">
          <div className="personal-card-title">
            <span className="iconfont icon-moneycollect" aria-hidden="true" />
            <h2>充值积分</h2>
          </div>
          <div className="provider-switch">
            <button type="button" className="is-wechat is-unavailable" disabled title="微信支付暂未开放">
              <span className={providerMeta.wechat.icon} aria-hidden="true" />
              微信支付
              <small>暂未开放</small>
            </button>
            <button type="button" className="is-alipay is-active" aria-pressed="true">
              <span className={providerMeta.alipay.icon} aria-hidden="true" />
              支付宝
            </button>
          </div>
          <div className="topup-products">
            {summary?.topup_products.map((item) => (
              <button
                key={item.package_id}
                type="button"
                className={activePackage?.package_id === item.package_id ? "is-active" : ""}
                onClick={() => setSelectedPackage(item.package_id)}
              >
                <strong>{item.points.toLocaleString()} 积分</strong>
                <span>{money(item.amount_cents)}</span>
              </button>
            ))}
          </div>
          <label className={`custom-topup ${selectedPackage === "custom" ? "is-active" : ""}`}>
            <span>自定义金额</span>
            <div>
              <b>¥</b>
              <input
                type="number"
                min="1"
                max="3000"
                step="1"
                inputMode="numeric"
                value={customAmount}
                onFocus={() => setSelectedPackage("custom")}
                onChange={(event) => {
                  setCustomAmount(event.target.value);
                  setSelectedPackage("custom");
                }}
                placeholder="1 - 3000"
                aria-label="自定义充值金额，单位元"
              />
              <em>元</em>
            </div>
            <small>{customAmount ? (customAmountCents ? `预计到账 ${activePackage?.points.toLocaleString()} 积分` : "请输入 1 到 3000 的整数金额") : "支持 1 - 3000 元整数充值"}</small>
          </label>
          <button className="primary-topup" type="button" disabled={!activePackage || creating} onClick={() => void submitTopup(activePackage)}>
            {creating ? "正在创建服务器订单..." : "创建充值订单"}
          </button>
          {createdOrder && (
            <div className="payment-result">
              <strong>订单已创建：{createdOrder.order.out_trade_no}</strong>
              <span>{createdOrder.payment.message}</span>
            </div>
          )}
          {paymentNotice && <p className="payment-notice">{paymentNotice}</p>}
        </article>

        <div className="personal-stack">
        <article className="personal-card orders-card">
          <div className="personal-card-title">
            <span className="iconfont icon-accountbook-fill" aria-hidden="true" />
            <h2>最近订单</h2>
          </div>
          <div className="order-list">
            {summary?.recent_orders.length ? summary.recent_orders.map((order) => (
              <div key={order.order_id} className="order-row">
                <div>
                  <strong>{providerMeta[order.provider]?.label ?? order.provider} · {statusLabel(order.status)}</strong>
                  <span>{order.out_trade_no}</span>
                </div>
                <div>
                  <b>{money(order.amount_cents)}</b>
                  <span>+{order.points.toLocaleString()} 积分</span>
                </div>
              </div>
            )) : <p className="empty-orders">暂无充值订单</p>}
          </div>
        </article>

        <article className="personal-card security-card">
          <div className="personal-card-title">
            <span className="iconfont icon-safetycertificate" aria-hidden="true" />
            <h2>安全策略</h2>
          </div>
          <ul>
            <li>积分余额、充值订单、扣费记录全部存放在平台服务器。</li>
            <li>微信/支付宝回调未完成验签前，订单不会入账。</li>
            <li>账本按用户账号和工作区关联，后续扣费接口必须带幂等键。</li>
            <li>本地数据库或浏览器缓存被改，不影响服务器余额。</li>
          </ul>
        </article>
        </div>
        </div> : activePanel === "pricing" ? (
          <article className="personal-card pricing-card">
            <div className="personal-card-title">
              <span className="iconfont icon-calculator" aria-hidden="true" />
              <div><h2>计费规则</h2><small>服务端权威定价，按规则版本生效，客户端不参与报价。</small></div>
              <button type="button" onClick={() => refreshBalance()} disabled={balanceCooldownActive || loading}>
                {balanceCooldownActive ? `${balanceCooldownSeconds} 秒后` : "刷新"}
              </button>
            </div>

            <div className="pricing-hero">
              <span className="pricing-hero-kicker">单条处理链接 · 消费定价</span>
              <div className="pricing-hero-range">
                <b>{summary?.pricing.product_link.actual_charge_min_points.toLocaleString() ?? "--"}</b>
                <em>~</em>
                <b>{summary?.pricing.product_link.actual_charge_max_points.toLocaleString() ?? "--"}</b>
                <i>积分 / 条</i>
              </div>
              <p className="pricing-hero-note">
                受服务商模型波动影响，单条链接定价在{" "}
                {summary?.pricing.product_link.actual_charge_min_points ?? "--"} 积分到{" "}
                {summary?.pricing.product_link.actual_charge_max_points ?? "--"} 积分区间波动哦~
              </p>
            </div>

            <div className="pricing-feature-grid">
              {pricingFeatures.map(({ key, label, note }) => {
                const feature = summary?.pricing.features[key];
                if (!feature) return null;
                return (
                  <div key={key} className="pricing-feature">
                    <span className="pricing-feature-name">
                      <b>{label}</b>
                      <small>{note}</small>
                    </span>
                    <span className="pricing-feature-points">
                      <b>{feature.charge_points.toLocaleString()}</b>
                      <i>积分 / 条</i>
                      <small>预冻结 {feature.reserve_points.toLocaleString()}</small>
                    </span>
                  </div>
                );
              })}
            </div>

            <div className="pricing-foot">
              <span>充值换算：{summary?.pricing.ratio_label ?? "1 元 = 100 积分"}</span>
              <span>规则版本 v{summary?.pricing.rule_version ?? "--"}{summary?.pricing.effective_at ? ` · 生效于 ${summary.pricing.effective_at.replace("T", " ").slice(0, 16)}` : ""}</span>
            </div>
          </article>
        ) : (
          <article className="personal-card usage-card">
            <div className="personal-card-title">
              <span className="iconfont icon-accountbook-fill" aria-hidden="true" />
              <div><h2>消费流水</h2><small>每条记录包含冻结、实际扣费、释放、模型与结算状态。</small></div>
              <button type="button" onClick={() => loadUsage(true)}>刷新</button>
            </div>
            <div className="usage-filters">
              <label>
                <span>开始日期</span>
                <input
                  type="date"
                  value={filterDateFrom}
                  max={filterDateTo || undefined}
                  onChange={(event) => setFilterDateFrom(event.target.value)}
                />
              </label>
              <label>
                <span>结束日期</span>
                <input
                  type="date"
                  value={filterDateTo}
                  min={filterDateFrom || undefined}
                  onChange={(event) => setFilterDateTo(event.target.value)}
                />
              </label>
              <label>
                <span>服务</span>
                <select
                  value={filterFeature}
                  onChange={(event) => setFilterFeature(event.target.value)}
                >
                  <option value="">全部服务</option>
                  <option value="product_processing.image_grid_2k">智能生图</option>
                  <option value="product_processing.text">商品文本</option>
                  <option value="product_processing.batch">批量链接处理</option>
                </select>
              </label>
              <label>
                <span>状态</span>
                <select
                  value={filterStatus}
                  onChange={(event) => setFilterStatus(event.target.value)}
                >
                  <option value="">全部状态</option>
                  <option value="succeeded">已结算</option>
                  <option value="reserved,frozen">处理中</option>
                  <option value="failed">已释放</option>
                </select>
              </label>
              {hasUsageFilter && (
                <button type="button" className="usage-filter-reset" onClick={resetUsageFilters}>
                  重置筛选
                </button>
              )}
            </div>
            {usageLoading && <p className="usage-state">正在读取服务器消费账本…</p>}
            {usageError && <p className="usage-state is-error">{usageError}</p>}
            {!usageLoading && !usageError && (
              <div className="usage-table-wrap">
                <table className="usage-table">
                  <thead><tr><th>时间</th><th>服务</th><th>状态</th><th>冻结</th><th>实际扣费</th><th>释放</th><th>规则</th><th>调用信息</th></tr></thead>
                  <tbody>{usageEntries.length ? usageEntries.map((entry) => (
                    <tr key={entry.usage_id}>
                      <td><b>{formatUsageTime(entry.created_at)}</b><small>{entry.source_ref || entry.usage_id}</small></td>
                      <td>{usageServiceLabel(entry.feature_key)}{usageDetailText(entry) && <small>{usageDetailText(entry)}</small>}</td>
                      <td><span className={`usage-status is-${entry.status}`}>{entry.status === "succeeded" ? "已结算" : entry.status === "reserved" || entry.status === "frozen" ? "处理中" : "已释放"}</span>{entry.error_message && <small>{entry.error_message}</small>}</td>
                      <td>{entry.reserved_points}</td><td>{entry.charged_points}</td><td>{entry.refunded_points}</td>
                      <td>{entry.rule_version ? `v${entry.rule_version}` : "—"}</td><td><small>{entry.usage_id.slice(0, 14)}…</small></td>
                    </tr>
                  )) : <tr><td colSpan={8} className="usage-empty">{hasUsageFilter ? "没有匹配的消费流水，试试调整筛选条件" : "暂无消费流水"}</td></tr>}</tbody>
                </table>
              </div>
            )}
          </article>
        )}
        </div>
      </div>
    </section>
  );
}
