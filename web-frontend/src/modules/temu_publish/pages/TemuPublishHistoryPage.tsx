import { useCallback, useEffect, useMemo, useState } from "react";

import "../styles/temuPublish.css";

import { temuPublishApi } from "../api/temuPublishApi";
import type { TemuPublishHistoryItem } from "../types";

const PAGE_SIZE = 30;

type Tab = "success" | "failed";

/**
 * 发布历史：成功与失败分开看。
 *
 * 为什么单独一页：失败原因（Temu 原话 + 错误码）和成功后的商品 id 是两类不同的东西 ——
 * 混在一起时用户既找不到「哪条失败了要修」，也找不到「上周发的那件 id 是多少」。
 * 记录来自后端台账 `temu_publish_submissions`，本机清缓存/换机器都还在。
 */
export function TemuPublishHistoryPage({
  onOpenDraftLibrary,
}: {
  /** 失败那条要重发时，跳回草稿库挑成品 */
  onOpenDraftLibrary?: () => void;
}) {
  const [tab, setTab] = useState<Tab>("success");
  const [items, setItems] = useState<TemuPublishHistoryItem[]>([]);
  const [counts, setCounts] = useState({ success: 0, failed: 0 });
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);

  const load = useCallback(
    async (nextTab: Tab, nextPage: number, append: boolean) => {
      setLoading(true);
      setError("");
      try {
        const data = await temuPublishApi.publishHistory({
          status: nextTab,
          page: nextPage,
          pageSize: PAGE_SIZE,
        });
        setCounts(data.counts);
        setTotal(data.total);
        setPage(nextPage);
        setItems((current) => (append ? [...current, ...data.items] : data.items));
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void load(tab, 1, false);
  }, [load, tab]);

  const remain = Math.max(0, total - items.length);
  const emptyText = useMemo(
    () =>
      tab === "success"
        ? "还没有成功发布过商品。从草稿库挑一条发布成功后，这里会记下商品 id。"
        : "还没有失败记录 —— 失败时这里会显示 Temu 的原话和错误码，方便照着改。",
    [tab],
  );

  return (
    <div className="temu-history-page">
      <header className="temu-history-head">
        <div>
          <h1>发布历史</h1>
          <p>成功与失败分开记；成功过的草稿会自动从草稿库移除，避免重复上架。</p>
        </div>
        <div className="temu-history-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "success"}
            className={`temu-history-tab${tab === "success" ? " is-active" : ""}`}
            onClick={() => setTab("success")}
          >
            成功 <em>{counts.success}</em>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "failed"}
            className={`temu-history-tab${tab === "failed" ? " is-active" : ""}`}
            onClick={() => setTab("failed")}
          >
            失败 <em>{counts.failed}</em>
          </button>
        </div>
      </header>

      {error ? (
        <p className="temu-history-error" role="alert">
          {error}
        </p>
      ) : null}

      {!loading && !items.length ? <p className="temu-history-empty">{emptyText}</p> : null}

      <ul className="temu-history-list">
        {items.map((item) => {
          const expanded = openId === item.id;
          return (
            <li key={item.id} className={`temu-history-row is-${item.status}`}>
              <div className="temu-history-main">
                <span className={`temu-history-badge is-${item.status}`}>
                  {item.status === "success" ? "成功" : "失败"}
                </span>
                <div className="temu-history-text">
                  <strong title={item.title}>{item.title || "（无标题）"}</strong>
                  <span className="temu-history-meta">
                    {item.created_at}
                    {item.leaf_cat_id ? ` · 类目 ${item.leaf_cat_id}` : ""}
                    {item.sku_count ? ` · ${item.sku_count} 个 SKU` : ""}
                  </span>
                </div>
                {item.status === "success" ? (
                  <span className="temu-history-gid" title="Temu 商品 id">
                    id {item.goods_id || "—"}
                  </span>
                ) : (
                  <button
                    type="button"
                    className="temu-btn temu-btn-sm"
                    onClick={() => setOpenId(expanded ? null : item.id)}
                  >
                    {expanded ? "收起原因" : "看原因"}
                  </button>
                )}
              </div>

              {item.status === "failed" ? (
                expanded ? (
                  <div className="temu-history-detail">
                    <p className="temu-history-reason">{item.error || "（没有返回原因）"}</p>
                    {onOpenDraftLibrary ? (
                      <button type="button" className="temu-btn temu-btn-sm" onClick={onOpenDraftLibrary}>
                        去草稿库重发
                      </button>
                    ) : null}
                  </div>
                ) : null
              ) : null}
            </li>
          );
        })}
      </ul>

      {remain > 0 ? (
        <div className="temu-history-more">
          <button
            type="button"
            className="temu-btn"
            disabled={loading}
            onClick={() => void load(tab, page + 1, true)}
          >
            {loading ? "加载中…" : `加载更多（还有 ${remain} 条）`}
          </button>
        </div>
      ) : null}

      {loading && !items.length ? <p className="temu-history-empty">加载中…</p> : null}
    </div>
  );
}
