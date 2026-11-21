import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { temuPublishApi } from "../api/temuPublishApi";
import type { TemuLowStockResult } from "../types";

/**
 * 低库存（零库存 SKU）清单：扫店铺商品，列出存在零库存 SKU 的商品。
 *
 * 后端 /goods/low-stock 与前端 API 早已就位但一直没界面（2026-11-21 补上）。
 * 改库存接口（bg.btg.goods.stock.quantity.update）服务商侧未开通，
 * 所以这里只出清单——提醒用户到 Temu 卖家中心补库存，不写任何数据。
 */
export function LowStockDialog({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<TemuLowStockResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setData(await temuPublishApi.lowStockGoods({ limit: 60 }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "扫描失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return createPortal(
    <div className="temu-edit-backdrop" onClick={onClose}>
      <div
        className="temu-edit-dialog temu-lowstock-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="低库存清单"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="temu-edit-head">
          <div>
            <strong>低库存清单</strong>
            <span>存在零库存 SKU 的商品；断货会导致排名下降，请到 Temu 卖家中心补库存</span>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        <div className="temu-lowstock-body">
          {loading ? <p className="temu-muted">正在扫描店铺商品（每页约 1.5 秒）…</p> : null}

          {error ? <p className="temu-confirm-warn">{error}</p> : null}

          {!loading && !error && data ? (
            <>
              <p className="temu-muted">
                已扫描 {data.scanned} / 共 {data.total} 个商品；
                其中 {data.items.length} 个存在零库存 SKU（共 {data.zero_sku_count} 个 SKU 为 0）。
                {data.can_update_stock ? "" : " 目前不支持在本工具内改库存，请到 Temu 卖家中心调整。"}
              </p>
              {data.items.length ? (
                <table className="temu-lowstock-table">
                  <thead>
                    <tr>
                      <th>商品</th>
                      <th>货号</th>
                      <th>库存合计</th>
                      <th>零库存 SKU</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((item) => (
                      <tr key={item.product_id}>
                        <td className="temu-lowstock-title" title={item.title}>
                          {item.title || item.product_id}
                        </td>
                        <td>{item.skc_ext_code || "—"}</td>
                        <td>{item.stock_total ?? "—"}</td>
                        <td>{item.zero_stock_sku_count ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="temu-muted">未发现零库存 SKU，库存状况良好。</p>
              )}
            </>
          ) : null}
        </div>

        <footer className="temu-edit-foot">
          <button type="button" className="temu-btn" onClick={() => void load()} disabled={loading}>
            {loading ? "扫描中…" : "重新扫描"}
          </button>
          <button type="button" className="temu-btn" onClick={onClose}>
            关闭
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
