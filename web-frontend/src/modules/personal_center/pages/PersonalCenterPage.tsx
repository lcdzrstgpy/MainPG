import { useEffect, useMemo, useState } from "react";

import { getAuthAccount } from "../../../transport/http/client";
import { createTopupOrder, loadBillingSummary, type BillingPackage, type BillingSummary, type TopupOrderResponse } from "../api/personalCenterApi";
import "../styles/personalCenter.css";

type AccountSnapshot = {
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedPackage, setSelectedPackage] = useState("");
  const [provider, setProvider] = useState<"wechat" | "alipay">("wechat");
  const [creating, setCreating] = useState(false);
  const [createdOrder, setCreatedOrder] = useState<TopupOrderResponse | null>(null);
  const account = getAuthAccount<AccountSnapshot>();

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
        <div className="personal-security-pill">
          <span className="iconfont icon-lock-fill" aria-hidden="true" />
          <strong>服务器账本校验</strong>
          <span>本地数据不作为余额依据</span>
        </div>
      </div>

      {error && <div className="personal-alert is-error">{error}</div>}
      {loading && <div className="personal-alert">正在读取服务器账户与积分数据...</div>}

      <div className="personal-grid">
        <article className="personal-card wallet-card">
          <div className="personal-card-title">
            <span className="iconfont icon-wallet-fill" aria-hidden="true" />
            <h2>积分钱包</h2>
            <button type="button" onClick={refresh}>刷新</button>
          </div>
          <div className="wallet-balance">
            <span>可用积分</span>
            <strong>{summary?.wallet.available_points.toLocaleString() ?? "--"}</strong>
          </div>
          <div className="wallet-metrics">
            <div><span>总积分</span><b>{summary?.wallet.points_balance.toLocaleString() ?? "--"}</b></div>
            <div><span>冻结积分</span><b>{summary?.wallet.locked_points.toLocaleString() ?? "--"}</b></div>
            <div><span>换算比例</span><b>{summary?.pricing.ratio_label ?? "1 元 = 100 积分"}</b></div>
          </div>
          <p className="wallet-note">充值、扣费、退款都以服务器账本为准；客户端仅展示结果，不能本地改余额。</p>
        </article>

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
      </div>
    </section>
  );
}
