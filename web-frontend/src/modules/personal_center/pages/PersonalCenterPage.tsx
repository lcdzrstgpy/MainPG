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

export function PersonalCenterPage() {
  const [summary, setSummary] = useState<BillingSummary | null>(null);
  const [activePanel, setActivePanel] = useState<"wallet" | "usage">("wallet");
  const [usageEntries, setUsageEntries] = useState<BillingUsageEntry[]>([]);
  const [usageLoading, setUsageLoading] = useState(false);
  const [usageError, setUsageError] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedPackage, setSelectedPackage] = useState("");
  const [provider, setProvider] = useState<"wechat" | "alipay">("wechat");
  const [creating, setCreating] = useState(false);
  const [createdOrder, setCreatedOrder] = useState<TopupOrderResponse | null>(null);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [passwordSuccess, setPasswordSuccess] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const account = getAuthAccount<AccountSnapshot>();
  // 消费流水刷新保护时间：切换「消费流水」页签时，距上次请求小于该时长则直接复用已加载数据，不重复请求。
  const USAGE_REFRESH_COOLDOWN_MS = 30_000;
  const lastUsageFetchAt = useRef(0);
  // 消费流水筛选条件（服务/状态/日期）；筛选变更时强制重新拉取。
  const [filterFeature, setFilterFeature] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterDateFrom, setFilterDateFrom] = useState("");
  const [filterDateTo, setFilterDateTo] = useState("");

  const loadUsage = useCallback((force = false) => {
    if (!force && Date.now() - lastUsageFetchAt.current < USAGE_REFRESH_COOLDOWN_MS) {
      return;
    }
    lastUsageFetchAt.current = Date.now();
    setUsageLoading(true);
    setUsageError("");
    loadBillingUsageHistory({
      featureKey: filterFeature || undefined,
      usageStatus: filterStatus || undefined,
      dateFrom: filterDateFrom || undefined,
      dateTo: filterDateTo || undefined,
    })
      .then((payload) => setUsageEntries(payload.items))
      .catch((exc) => setUsageError(exc instanceof Error ? exc.message : "读取消费流水失败"))
      .finally(() => setUsageLoading(false));
  }, [filterFeature, filterStatus, filterDateFrom, filterDateTo]);

  const hasUsageFilter = Boolean(filterFeature || filterStatus || filterDateFrom || filterDateTo);
  const resetUsageFilters = () => {
    setFilterFeature("");
    setFilterStatus("");
    setFilterDateFrom("");
    setFilterDateTo("");
    loadUsage(true);
  };

  const activePackage = useMemo(
    () => summary?.topup_products.find((item) => item.package_id === selectedPackage) ?? summary?.topup_products[0],
    [selectedPackage, summary],
  );

  const refresh = () => {
    setLoading(true);
    setError("");
    loadBillingSummary()
      .then((payload) => {
        setSummary(payload);
        setSelectedPackage((current) => current || payload.topup_products[0]?.package_id || "");
      })
      .catch((exc) => setError(exc instanceof Error ? exc.message : "读取个人中心失败"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
  }, []);

  useEffect(() => {
    if (activePanel !== "usage") return;
    loadUsage(false);
  }, [activePanel, loadUsage]);

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
    setCreatedOrder(null);
    try {
      const response = await createTopupOrder({ provider, package_id: product.package_id });
      setCreatedOrder(response);
      await loadBillingSummary().then(setSummary);
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
          <button type="button" className="personal-stats-refresh" onClick={refresh} aria-label="刷新余额">↻ 刷新</button>
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
            {(Object.keys(providerMeta) as Array<keyof typeof providerMeta>).map((key) => {
              const meta = providerMeta[key];
              return (
                <button
                  key={key}
                  type="button"
                  className={`${meta.className} ${provider === key ? "is-active" : ""}`}
                  onClick={() => setProvider(key)}
                >
                  <span className={meta.icon} aria-hidden="true" />
                  {meta.label}
                </button>
              );
            })}
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
          <button className="primary-topup" type="button" disabled={!activePackage || creating} onClick={() => void submitTopup(activePackage)}>
            {creating ? "正在创建服务器订单..." : "创建充值订单"}
          </button>
          {createdOrder && (
            <div className="payment-result">
              <strong>订单已创建：{createdOrder.order.out_trade_no}</strong>
              <span>{createdOrder.payment.message}</span>
            </div>
          )}
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
        </div> : (
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
                  onChange={(event) => {
                    setFilterDateFrom(event.target.value);
                    loadUsage(true);
                  }}
                />
              </label>
              <label>
                <span>结束日期</span>
                <input
                  type="date"
                  value={filterDateTo}
                  min={filterDateFrom || undefined}
                  onChange={(event) => {
                    setFilterDateTo(event.target.value);
                    loadUsage(true);
                  }}
                />
              </label>
              <label>
                <span>服务</span>
                <select
                  value={filterFeature}
                  onChange={(event) => {
                    setFilterFeature(event.target.value);
                    loadUsage(true);
                  }}
                >
                  <option value="">全部服务</option>
                  <option value="product_processing.image_grid_2k">四宫格生图</option>
                  <option value="product_processing.text">商品文本</option>
                  <option value="product_processing.batch">批量链接处理</option>
                </select>
              </label>
              <label>
                <span>状态</span>
                <select
                  value={filterStatus}
                  onChange={(event) => {
                    setFilterStatus(event.target.value);
                    loadUsage(true);
                  }}
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
                      <td><b>{entry.created_at.replace("T", " ").slice(0, 19)}</b><small>{entry.source_ref || entry.usage_id}</small></td>
                      <td>{entry.feature_key === "product_processing.image_grid_2k" ? "四宫格生图" : entry.feature_key === "product_processing.batch" ? "批量链接处理" : "商品文本"}<small>{entry.model || entry.provider || "等待上游"}</small></td>
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
