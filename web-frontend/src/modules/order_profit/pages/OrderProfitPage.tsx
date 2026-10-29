import { useEffect, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import { orderProfitApi } from "../api/orderProfitApi";
import { OrderProfitAccountsTab } from "./OrderProfitAccountsTab";
import { OrderProfitCaptureTab } from "./OrderProfitCaptureTab";
import { OrderProfitFinanceTab } from "./OrderProfitFinanceTab";
import { OrderProfitOrdersTab } from "./OrderProfitOrdersTab";
import { OrderProfitRecordsTab } from "./OrderProfitRecordsTab";
import { OrderProfitSalesTab } from "./OrderProfitSalesTab";
import { OrderProfitSettlementTab } from "./OrderProfitSettlementTab";
import { OrderProfitStoreDataTab } from "./OrderProfitStoreDataTab";
import { OrderProfitTrafficTab } from "./OrderProfitTrafficTab";
import type { OrderProfitCaptureStatus, ProfitShop } from "../types";
import "../styles/orderProfit.css";

/**
 * 店铺数据侧栏合并成四个板块：
 * 经营概览（首页/流量/销量）、订单列表、账户资金（对账/结算）、数据采集。
 * 旧的单页 id 仍然保留在类型里，只用于兼容浏览器里已保存的旧页签。
 */
export type OrderProfitView =
  | "overview"
  | "orders"
  | "funds"
  | "capture"
  | "records"
  | "accounts"
  | "home"
  | "traffic"
  | "sales"
  | "recon"
  | "settlement";

type BoardView = "overview" | "orders" | "funds" | "capture" | "records" | "accounts";

type SubTab = { key: string; label: string; resource?: string; hint?: string };

const VIEW_META: Record<BoardView, { title: string; hint: string }> = {
  overview: { title: "经营概览", hint: "店铺首页指标、商品/店铺流量与销量库存水位" },
  orders: { title: "订单列表", hint: "全部、待发货、已发货、已签收、已取消订单的利润明细" },
  funds: { title: "账户资金", hint: "对账中心财务流水与按 PO 单的结算回款" },
  capture: { title: "数据采集", hint: "插件会话、一键采集与采集进度" },
  records: { title: "采集记录", hint: "最近一次入库记录与采集批次" },
  accounts: { title: "店铺账号", hint: "多账号独立浏览器、自动登录与验证码在线等待" },
};

const SUB_TABS: Record<BoardView, SubTab[]> = {
  overview: [
    { key: "store", label: "店铺首页", resource: "store" },
    { key: "traffic", label: "流量分析", resource: "traffic" },
    { key: "sales", label: "销量分析", resource: "sales" },
  ],
  orders: [],
  funds: [
    { key: "finance", label: "对账流水", resource: "finance" },
    { key: "settlement", label: "结算回款", resource: "settlement" },
  ],
  capture: [],
  records: [],
  accounts: [],
};

/** 旧版二级入口 → 合并后的板块 + 默认子页签 */
function resolveView(view: OrderProfitView): { board: BoardView; sub: string } {
  switch (view) {
    case "home":
      return { board: "overview", sub: "store" };
    case "traffic":
      return { board: "overview", sub: "traffic" };
    case "sales":
      return { board: "overview", sub: "sales" };
    case "recon":
      return { board: "funds", sub: "finance" };
    case "settlement":
      return { board: "funds", sub: "settlement" };
    default:
      return { board: view as BoardView, sub: "" };
  }
}

export function OrderProfitPage({
  isActive = true,
  view = "overview",
  onOpenCapture,
  onOpenAccounts,
}: {
  isActive?: boolean;
  view?: OrderProfitView;
  onOpenCapture?: () => void;
  onOpenAccounts?: () => void;
}) {
  const { board, sub } = resolveView(view);
  const [dataVersion, setDataVersion] = useState(0);
  const [shops, setShops] = useState<ProfitShop[]>([]);
  // "" = 跟随工作台当前绑定店铺；"all" = 全部店铺总览；其他 = 指定店铺名
  const [shopScope, setShopScope] = useState("");
  const [updating, setUpdating] = useState(false);
  const [activeCommandId, setActiveCommandId] = useState<number | null>(null);
  const [progress, setProgress] = useState<OrderProfitCaptureStatus["progress"]>(null);
  const [subView, setSubView] = useState(sub || SUB_TABS[board][0]?.key || "");
  const meta = VIEW_META[board] || VIEW_META.overview;
  const tabs = SUB_TABS[board];
  const resource = tabs.find((tab) => tab.key === subView)?.resource;

  // 从侧栏切到另一个板块时，默认子页签跟着变化。
  useEffect(() => {
    setSubView(sub || SUB_TABS[board][0]?.key || "");
  }, [board, sub]);

  // 页头店铺下拉：可切到任意已绑定店铺，也可以切「总览」看全部店铺。
  useEffect(() => {
    let cancelled = false;
    orderProfitApi
      .listShops()
      .then((payload) => {
        if (cancelled) return;
        setShops(payload.shops || []);
        setShopScope((current) => {
          if (current) return current;
          return payload.active?.name || payload.shops?.[0]?.name || "all";
        });
      })
      .catch(() => {
        if (!cancelled) setShops([]);
      });
    return () => {
      cancelled = true;
    };
  }, [dataVersion]);

  const handleCaptured = () => {
    setDataVersion((value) => value + 1);
  };

  /** 页头切店铺 = 切全局「当前采集店铺」，采集页与各数据页一起刷新。 */
  const handleShopScopeChange = async (value: string) => {
    setShopScope(value);
    setDataVersion((version) => version + 1);
    const target = shops.find((shop) => shop.name === value);
    if (!target?.id || target.is_active) return;
    try {
      const payload = await orderProfitApi.activateShop(target.id);
      const backfilled = Number(payload.backfilled || 0);
      showToast(
        `当前采集店铺已切换为「${payload.shop?.name || value}」`
          + (backfilled ? `，${backfilled} 行未归类数据已归入该店铺` : ""),
        "success",
      );
      setDataVersion((version) => version + 1);
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    }
  };

  // 页面级「更新」：只采当前子页签这一块数据（首次全量请用「去数据采集」）。
  const updateCurrent = async () => {
    if (!resource || updating || !isActive) return;
    setUpdating(true);
    setProgress(null);
    try {
      const payload = await orderProfitApi.listSessions();
      const session = (payload.sessions || []).find((item) => item.online)
        || (payload.sessions || [])[0];
      if (!session) {
        showToast("插件未连接：请先在浏览器插件弹窗点「连接插件」", "error");
        return;
      }
      const command = await orderProfitApi.startResourceCapture(session.session_id, resource);
      setActiveCommandId(command.command_id);
      for (let attempt = 0; attempt < 200; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
        const status = await orderProfitApi.captureStatus(command.command_id);
        setProgress(status.progress || null);
        if (["succeeded", "failed", "cancelled"].includes(status.status)) {
          showToast(status.status_text || (status.ok ? "更新完成" : "更新失败"), status.ok ? "success" : "error");
          if (status.ok) handleCaptured();
          return;
        }
      }
      showToast("更新超时，请到「数据采集」查看进度", "error");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setUpdating(false);
      setActiveCommandId(null);
    }
  };

  const stopCurrent = async () => {
    if (!activeCommandId) return;
    try {
      await orderProfitApi.cancelCapture(activeCommandId);
      showToast("已请求终止更新", "info");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    }
  };

  return (
    <div className="order-profit-page">
      <header className={`op-head has-art is-${board}`}>
        <span className="op-head-art" aria-hidden="true" />
        <div className="op-head-copy">
          <h1>{meta.title}</h1>
          <p>{meta.hint}</p>
        </div>
        <div className="op-head-actions">
          <label className="op-shop-switch">
            <span>店铺</span>
            <select
              value={shopScope}
              onChange={(event) => void handleShopScopeChange(event.target.value)}
              aria-label="切换店铺"
            >
              <option value="all">总览（全部店铺）</option>
              {shops.map((shop) => (
                <option key={shop.id || shop.name} value={shop.name}>
                  {shop.name}
                </option>
              ))}
            </select>
          </label>
          {resource ? (
            updating ? (
              <button type="button" className="op-btn" onClick={() => void stopCurrent()}>
                终止更新
              </button>
            ) : (
              <button type="button" className="op-btn" onClick={() => void updateCurrent()}>
                更新这一页
              </button>
            )
          ) : null}
          {onOpenCapture && board !== "capture" ? (
            <button type="button" className="op-btn op-btn-primary" onClick={onOpenCapture}>
              去数据采集
            </button>
          ) : null}
        </div>
      </header>

      {tabs.length > 1 ? (
        <div className="op-tabs">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={`op-tab ${subView === tab.key ? "is-active" : ""}`}
              onClick={() => setSubView(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      ) : null}

      {updating ? (
        <div className="op-card op-inline-progress">
          <div className="op-progress-head">
            <span>
              {progress?.step_label || "正在采集这一页数据"}
              {progress?.step_total ? `（${progress.step}/${progress.step_total}）` : ""}
            </span>
            <strong>{progress?.percent ?? 0}%</strong>
          </div>
          <div className="op-progress-track">
            <div className="op-progress-bar" style={{ width: `${progress?.percent ?? 0}%` }} />
          </div>
          {progress?.detail ? <small>{progress.detail}</small> : null}
        </div>
      ) : null}

      <main className="op-main">
        {board === "overview" && subView === "store" ? (
          <OrderProfitStoreDataTab isActive={isActive} refreshToken={dataVersion} shop={shopScope} />
        ) : null}
        {board === "overview" && subView === "traffic" ? (
          <OrderProfitTrafficTab isActive={isActive} refreshToken={dataVersion} shop={shopScope} />
        ) : null}
        {board === "overview" && subView === "sales" ? (
          <OrderProfitSalesTab isActive={isActive} refreshToken={dataVersion} shop={shopScope} />
        ) : null}
        {board === "orders" ? (
          <OrderProfitOrdersTab isActive={isActive} refreshToken={dataVersion} shop={shopScope} />
        ) : null}
        {board === "funds" && subView === "finance" ? (
          <OrderProfitFinanceTab isActive={isActive} refreshToken={dataVersion} shop={shopScope} />
        ) : null}
        {board === "funds" && subView === "settlement" ? (
          <OrderProfitSettlementTab isActive={isActive} refreshToken={dataVersion} shop={shopScope} />
        ) : null}
        {board === "accounts" ? (
          <OrderProfitAccountsTab isActive={isActive} />
        ) : null}
        {board === "records" ? (
          <OrderProfitRecordsTab isActive={isActive} refreshToken={dataVersion} />
        ) : null}
        {board === "capture" ? (
          <OrderProfitCaptureTab
            isActive={isActive}
            onCaptured={handleCaptured}
            onOpenAccounts={onOpenAccounts}
            refreshToken={dataVersion}
            onShopChanged={(name) => setShopScope(name)}
          />
        ) : null}
      </main>
    </div>
  );
}
