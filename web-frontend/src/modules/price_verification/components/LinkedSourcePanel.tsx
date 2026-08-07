import { useMemo, useState } from "react";

import type { SkcSourceLink, SourceTopProfit } from "../types";
import { SectionHelp } from "./SectionHelp";

type Props = {
  links: SkcSourceLink[];
  onUnlink: (linkId: number) => Promise<void>;
};

function toNumber(value: unknown): number {
  if (typeof value === "number") return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
  if (value === null || value === undefined || value === "") return Number.POSITIVE_INFINITY;
  const parsed = Number(String(value).replace(/[¥,\s]/g, ""));
  return Number.isFinite(parsed) ? parsed : Number.POSITIVE_INFINITY;
}

function moneyText(value: unknown) {
  const number = toNumber(value);
  return Number.isFinite(number) ? `¥${number.toFixed(2)}` : "—";
}

function percentText(value: unknown) {
  const number = toNumber(value);
  return Number.isFinite(number) ? `${Math.round(number * 100)}%` : "";
}

function siteLabel(site?: string) {
  if (site === "US") return "美国站";
  if (site === "CO") return "哥伦比亚站";
  if (site === "EC") return "厄瓜多尔站";
  return site || "—";
}

function profitReasonText(reason?: string) {
  if (!reason) return "无法核算";
  if (reason === "missing_site") return "站点未识别";
  if (reason === "missing_selling_price") return "缺少调整后申报价";
  if (reason === "missing_source_price") return "关联价格缺失";
  if (reason === "profit_calculation_failed") return "利润计算失败";
  return reason;
}

function LinkProfit({ profit }: { profit: SourceTopProfit | null }) {
  if (!profit?.available) {
    return <span className="source-link-profit"><small className="is-muted">利润不可核算：{profitReasonText(profit?.reason)}</small></span>;
  }
  return <span className="source-link-profit"><b>{moneyText(profit.net_profit)}</b><small>净利 · {percentText(profit.profit_rate)}</small><em className={profit.qualified ? "is-qualified" : ""}>{profit.qualified ? "达标 ✓" : "未达标"}</em></span>;
}

export function LinkedSourcePanel({ links, onUnlink }: Props) {
  const [busyId, setBusyId] = useState<number | null>(null);

  const linkTime = (link: SkcSourceLink) => Date.parse(link.updated_at || link.created_at || "") || 0;

  const groups = useMemo(() => {
    const grouped: Record<string, SkcSourceLink[]> = {};
    for (const link of links) (grouped[link.skc_id] ||= []).push(link);
    return Object.entries(grouped)
      .map(([skcId, groupLinks]): [string, SkcSourceLink[]] => [skcId, [...groupLinks].sort((a, b) => linkTime(b) - linkTime(a))])
      .sort(([, a], [, b]) => linkTime(b[0]) - linkTime(a[0]));
  }, [links]);

  const unlink = async (link: SkcSourceLink) => {
    if (busyId) return;
    setBusyId(link.id);
    try {
      await onUnlink(link.id);
    } finally {
      setBusyId(null);
    }
  };

  return <section className="price-verification-panel price-verification-linked-panel"><div className="price-verification-panel-heading"><div><p className="eyebrow">STEP 04 · SOURCED</p><h2>已关联 1688 货源<SectionHelp title="集中展示所有已保留 SKC 关联的 1688 代发货源：SKC 标题、站点、调整后申报价一目了然，每条 1688 链接都按“候选源价（可调）”核算了利润（净利润 / 利润率 / 达标与否）。数据已入库，刷新页面不会丢失。" /></h2></div><div className="price-verification-source-empty"><div><span>已关联 SKC</span><strong>{groups.length}</strong></div><div><span>已关联 1688 链接</span><strong>{links.length}</strong></div></div></div>
    {groups.length ? <div className="price-verification-linked-groups">{groups.map(([skcId, groupLinks]) => {
      const first = groupLinks[0];
      return <div className="price-verification-linked-group" key={skcId}><div className="price-verification-source-group-head"><strong>{skcId} · {first?.product_title || "未命名商品"}</strong><small>{siteLabel(first?.site)} · 调整后申报价 {moneyText(first?.selling_price)} · 已关联 {groupLinks.length} 条 · 价格与利润已按候选源价（可调）校准</small></div><div className="price-verification-source-linked">{groupLinks.map((link) => <div key={link.id}><a href={link.source_url} target="_blank" rel="noreferrer"><img src={link.main_image_url} alt="" loading="lazy" referrerPolicy="no-referrer" /><span><b className="source-link-title">{link.source_title || link.offer_id}</b><small className="source-link-offer">offer {link.offer_id}</small></span></a><span className="source-link-source-price"><small>源价</small><b>¥{link.price_cny ?? "—"}</b></span><LinkProfit profit={link.profit ?? null} /><button onClick={() => void unlink(link)} disabled={busyId === link.id}>{busyId === link.id ? "解除中…" : "解除"}</button></div>)}</div></div>;
    })}</div> : <p className="price-verification-source-empty-line">暂无已关联的 1688 货源；在上方“货源匹配”板块对候选点“关联”后，这里会保留展示（已入库，刷新不丢失）。</p>}
  </section>;
}
