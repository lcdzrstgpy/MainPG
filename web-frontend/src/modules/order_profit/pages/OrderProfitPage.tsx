import { useEffect, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import { orderProfitApi } from "../api/orderProfitApi";
import { ActiveCaptureBanner } from "../components/ActiveCaptureBanner";
import { PageHero } from "../components/ShopDataPrimitives";
import { OrderProfitAccountsTab } from "./OrderProfitAccountsTab";
import { OrderProfitCaptureTab } from "./OrderProfitCaptureTab";
import { OrderProfitFinanceTab } from "./OrderProfitFinanceTab";
import { OrderProfitHotProductsTab } from "./OrderProfitHotProductsTab";
import { OrderProfitOrdersTab } from "./OrderProfitOrdersTab";
import { OrderProfitOverviewTab } from "./OrderProfitOverviewTab";
import { OrderProfitRecordsTab } from "./OrderProfitRecordsTab";
import { OrderProfitSalesTab } from "./OrderProfitSalesTab";
import { OrderProfitSettlementTab } from "./OrderProfitSettlementTab";
import { OrderProfitStoreDataTab } from "./OrderProfitStoreDataTab";
import { OrderProfitTrafficTab } from "./OrderProfitTrafficTab";
import type { OrderProfitCaptureStatus, ProfitShop } from "../types";
import "../styles/orderProfit.css";

/**
 * 店铺数据侧栏合并成四个板块：
 * 经营概览（总览/首页/流量/销量）、订单列表、账户资金（对账/结算）、数据采集。
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

const VIEW_META: Record<BoardView, { eyebrow: string; title: string; hint: string }> = {
  overview: {
    eyebrow: "看经营",
    title: "经营概览",
    hint: "先看总览判断今天该做什么，再进入首页指标、流量或销量找原因。",
  },
  orders: {
    eyebrow: "看订单",
    title: "订单列表",
    hint: "按状态和月份查看每一单的钱从哪来、成本花在哪、利润是否正常。",
  },
  funds: {
    eyebrow: "看资金",
    title: "账户资金",
    hint: "对账流水看费用扣在哪，结算回款看每个 PO 单最终到账多少。",
  },
  capture: {
    eyebrow: "管数据",
    title: "数据采集",
    hint: "先确认插件与店铺就绪，再同步数据；进度和结果都留在这一页。",
  },
  records: {
    eyebrow: "管数据",
    title: "入库记录",
    hint: "每次采集写入了多少订单，都在这里留下可追溯的批次。",
  },
  accounts: {
    eyebrow: "管账号",
    title: "店铺账号",
    hint: "先绑定店铺账号，采集才有明确的写入目标。",
  },
};

const SUB_TABS: Record<BoardView, SubTab[]> = {
  overview: [
    { key: "summary", label: "总览", hint: "今日概览与待办；具体原因去下面三个专题页。" },
    { key: "store", label: "店铺首页", resource: "store", hint: "待发货、在售商品与近 30 日销量，先看整体水位。" },
    { key: "traffic", label: "流量分析", resource: "traffic", hint: "曝光、点击、访客与渠道构成，判断流量从哪来。" },
    { key: "sales", label: "销量分析", resource: "sales", hint: "销量、售罄与库存健康，判断货卖得动不动。" },
  ],
  orders: [
    { key: "orders", label: "订单明细", hint: "按状态和月份查看每一单的钱从哪来、成本花在哪。" },
    { key: "hot", label: "爆品统计", hint: "选月份看卖得最好的商品链接与 SKU。" },
  ],
  funds: [
    { key: "finance", label: "对账流水", resource: "finance", hint: "推广服务费、罚款与结算的逐笔流水。" },
    { key: "settlement", label: "结算回款", resource: "settlement", hint: "按 PO 单查看销售与运费回款、冲回。" },
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
  onOpenOrders,
}: {
  isActive?: boolean;
  view?: OrderProfitView;
  onOpenCapture?: () => void;
  onOpenAccounts?: () => void;
  onOpenOrders?: () => void;
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
  const activeTab = tabs.find((tab) => tab.key === subView) || tabs[0];
  const resource = activeTab?.resource;
  // 二级页签下把子页名字带上，用户始终知道自己在哪一层。
  const heroTitle = tabs.length > 1 && activeTab && subView !== "summary"
    ? `${meta.title} · ${activeTab.label}`
    : meta.title;
  const heroDesc = tabs.length > 1 && activeTab && subView !== "summary"
    ? activeTab.hint || meta.hint
    : meta.hint;
  // 二级专题页用自己的插画，避免“流量/销量”还挂着经营总览的图。
  const heroArt = board === "overview" && (subView === "traffic" || subView === "sales") ? subView : board;

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
      <PageHero
        eyebrow={`店铺数据 · ${meta.eyebrow}`}
        title={heroTitle}
        desc={heroDesc}
        art={heroArt}
        aside={
          <>
            <span className="op-chip">{shops.length ? `${shops.length} 家店铺` : "尚未绑定店铺"}</span>
            <span className="op-chip">{shopScope === "all" ? "全部店铺" : shopScope || "跟随工作台"}</span>
          </>
        }
      />

      <div className="op-commandbar">
        <div className="op-commandbar-main">
          {board === "accounts" ? (
            <span className="op-commandbar-label">账号与权限按工作台账号维度管理</span>
          ) : (
            <label className="op-shop-switch">
              <span>当前店铺</span>
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
          )}
          {board === "overview" || board === "orders" || board === "funds" ? (
            <span className="op-commandbar-label">切换店铺会同步影响各数据页与采集写入目标</span>
          ) : null}
        </div>
        <div className="op-commandbar-actions">
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
      </div>

      {tabs.length > 1 ? (
        <nav className="op-subnav" aria-label="页面分区">
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
          {activeTab?.hint ? <span className="op-subnav-hint">{activeTab.hint}</span> : null}
        </nav>
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

      <ActiveCaptureBanner
        enabled={isActive && board !== "capture"}
        onOpenCapture={onOpenCapture}
        onFinished={handleCaptured}
      />

      <main className="op-main">
        {board === "overview" && subView === "summary" ? (
          <OrderProfitOverviewTab
            isActive={isActive}
            refreshToken={dataVersion}
            shop={shopScope}
            onNavigate={(target) => {
              if (target === "orders") {
                onOpenOrders?.();
                return;
              }
              setSubView(target === "home" ? "store" : target);
            }}
            onOpenOrders={onOpenOrders}
            onOpenCapture={onOpenCapture}
          />
        ) : null}
        {board === "overview" && subView === "store" ? (
          <OrderProfitStoreDataTab
            isActive={isActive}
            refreshToken={dataVersion}
            shop={shopScope}
            onOpenCapture={onOpenCapture}
          />
        ) : null}
        {board === "overview" && subView === "traffic" ? (
          <OrderProfitTrafficTab isActive={isActive} refreshToken={dataVersion} shop={shopScope} />
        ) : null}
        {board === "overview" && subView === "sales" ? (
          <OrderProfitSalesTab isActive={isActive} refreshToken={dataVersion} shop={shopScope} />
        ) : null}
        {board === "orders" && subView === "orders" ? (
          <OrderProfitOrdersTab
            isActive={isActive}
            refreshToken={dataVersion}
            shop={shopScope}
            onOpenCapture={onOpenCapture}
          />
        ) : null}
        {board === "orders" && subView === "hot" ? (
          <OrderProfitHotProductsTab
            isActive={isActive}
            refreshToken={dataVersion}
            shop={shopScope}
          />
        ) : null}
        {board === "funds" && subView === "finance" ? (
          <OrderProfitFinanceTab isActive={isActive} refreshToken={dataVersion} shop={shopScope} />
        ) : null}
        {board === "funds" && subView === "settlement" ? (
          <OrderProfitSettlementTab isActive={isActive} refreshToken={dataVersion} shop={shopScope} />
        ) : null}
        {board === "accounts" ? (
          <OrderProfitAccountsTab isActive={isActive} onOpenCapture={onOpenCapture} />
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
          />
        ) : null}
      </main>
    </div>
  );
}
