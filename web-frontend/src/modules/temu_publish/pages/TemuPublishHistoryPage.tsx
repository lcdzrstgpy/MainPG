import { useCallback, useEffect, useRef, useState } from "react";

import { httpBlob } from "../../../transport/http/client";
import { temuPublishApi } from "../api/temuPublishApi";
import type { TemuPublishHistoryItem } from "../types";
import {
  copyText,
  dayLabelOf,
  errorCodeOf,
  formatPriceRange,
  formatRelativeTime,
  sourceLabelOf,
  stripErrorCode,
} from "./historyFormat";
import "../styles/temuPublish.css";

const PAGE_SIZE = 30;
/** 搜索输入到真正发请求之间的等待：边打字边搜会把网关打成筛子。 */
const SEARCH_DEBOUNCE_MS = 320;

type Tab = "success" | "failed";

/** 公网图直接给 <img>；127/localhost 这类本地媒体要带登录态取 blob。 */
function isPublicHttp(path: string): boolean {
  if (!/^https?:\/\//i.test(path)) return false;
  return !/^https?:\/\/(127\.0\.0\.1|localhost)(:|\/|$)/i.test(path);
}

/** 滚到附近才去取图：一屏 30 条同时打 30 个鉴权请求，列表会顿一下。 */
function useNearViewport(): [(node: Element | null) => void, boolean] {
  const [visible, setVisible] = useState(false);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const ref = useCallback((node: Element | null) => {
    observerRef.current?.disconnect();
    observerRef.current = null;
    if (!node) return;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    observerRef.current = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observerRef.current?.disconnect();
          observerRef.current = null;
        }
      },
      { rootMargin: "300px" },
    );
    observerRef.current.observe(node);
  }, []);
  return [ref, visible];
}

/**
 * 历史列表的缩略图。
 *
 * 发布时存的是「当时那张主图」的地址：网店图是公网 https（直接显示），
 * 本地 AI/POD 产物是带签名的本地媒体地址 —— 签名两小时就过期，所以走 httpBlob
 * 带登录态取，和草稿库同一套逻辑。取不到就显示占位块，不留破图。
 */
function HistoryThumb({ path, alt }: { path: string; alt: string }) {
  const [thumbRef, inView] = useNearViewport();
  const [src, setSrc] = useState("");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    let objectUrl = "";
    setSrc("");
    setFailed(false);
    if (!path || !inView) return;
    if (isPublicHttp(path)) {
      setSrc(path);
      return;
    }
    void httpBlob(path)
      .then((blob) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path, inView]);

  if (!path) {
    return (
      <div className="temu-history-thumb is-empty" ref={thumbRef}>
        无图
      </div>
    );
  }
  // 加载失败（图床 403 / 文件被删）要在 src 有值时就拦住：
  // 否则 onError 之后 src 还在，会一直渲染同一个裂图。
  if (!src || failed) {
    return (
      <div
        className={`temu-history-thumb${failed ? " is-empty" : ""}`}
        ref={thumbRef}
        aria-busy={!failed}
      >
        {failed ? "已失效" : ""}
      </div>
    );
  }
  return (
    <img
      className="temu-history-thumb"
      src={src}
      alt={alt}
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      ref={thumbRef}
    />
  );
}

/**
 * 发布历史：成功与失败分开看。
 *
 * 为什么单独一页：失败原因（Temu 原话 + 错误码）和成功后的商品 id 是两类不同的东西 ——
 * 混在一起时用户既找不到「哪条失败了要修」，也找不到「上周发的那件 id 是多少」。
 * 记录来自后端台账 `temu_publish_submissions`，本机清缓存/换机器都还在。
 *
 * 列表里每条都尽量把「这是哪件商品、发到哪个店、卖多少钱、什么时候发的、
 * 错在哪」摆出来：只有标题和时间的列表，用户还得点回草稿库去认货。
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
  const [keyword, setKeyword] = useState("");
  const [appliedKeyword, setAppliedKeyword] = useState("");
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [copied, setCopied] = useState("");
  // 剪贴板被浏览器/WebView 拒绝时不能静默：用户点「复制 id」没反应会以为功能坏了
  const [copyError, setCopyError] = useState("");
  // 列表可能是几分钟前拉的：相对时间要有东西可参照，避免「刚刚」永远不刷新
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(
    async (nextTab: Tab, nextPage: number, append: boolean, query: string) => {
      setLoading(true);
      setError("");
      try {
        const data = await temuPublishApi.publishHistory({
          status: nextTab,
          q: query,
          page: nextPage,
          pageSize: PAGE_SIZE,
        });
        // 后端换版/网关返回空对象时别把 undefined 塞进 state：渲染期 `items.length`
        // 一抛错整棵树就卸载，用户看到的是整页白屏而不是「暂时没数据」。
        const rows = Array.isArray(data?.items) ? data.items : [];
        setCounts({
          success: Number(data?.counts?.success) || 0,
          failed: Number(data?.counts?.failed) || 0,
        });
        setTotal(Number(data?.total) || 0);
        setPage(nextPage);
        setItems((current) => (append ? [...current, ...rows] : rows));
        setNow(Date.now());
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  // 输入框防抖：320ms 内继续打字就不发请求
  useEffect(() => {
    const timer = window.setTimeout(() => setAppliedKeyword(keyword.trim()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [keyword]);

  useEffect(() => {
    void load(tab, 1, false, appliedKeyword);
  }, [load, tab, appliedKeyword]);

  /**
   * 老记录补图：「主图/类目名」是 2026-11-04 才进台账的列，之前的记录打开就是一片
   * 「无图」。第一次发现有「有商品 id 但没图」的成功记录时，让后端去店铺列表里
   * 按商品 id 认一遍（详情接口不返回图片），补完写回台账再刷新一次。
   *
   * 只在每次进入页面尝试一次：补不到（商品被删、网关抽风）不该反复打。
   */
  const backfillTried = useRef(false);
  useEffect(() => {
    if (backfillTried.current) return;
    const needs = items.some(
      (item) => item.status === "success" && item.goods_id && !item.image,
    );
    if (!needs) return;
    backfillTried.current = true;
    void temuPublishApi
      .backfillHistory()
      .then((result) => {
        if (result?.updated) void load(tab, 1, false, appliedKeyword);
      })
      .catch(() => {
        // 补图失败不影响看历史：占位方块继续显示
      });
  }, [items, load, tab, appliedKeyword]);

  const succeeded = counts.success;
  const failed = counts.failed;

  // 每个日期块有多少条：「今天 · 3 条」比一个孤零零的「今天」有用
  const dayCounts = new Map<string, number>();
  for (const item of items) {
    const label = dayLabelOf(item.created_ts, now);
    if (!label) continue;
    dayCounts.set(label, (dayCounts.get(label) || 0) + 1);
  }

  const emptyText = appliedKeyword
    ? `没有匹配「${appliedKeyword}」的记录，换个关键词试试（标题、商品 id、类目名都能搜）。`
    : tab === "success"
      ? "还没有成功发布过商品。从成品库挑一条发布成功后，这里会记下商品 id。"
      : "还没有失败记录 —— 失败时这里会显示 Temu 的原话和错误码，方便照着改。";

  async function handleCopy(key: string, text: string) {
    const ok = await copyText(text);
    if (!ok) {
      setCopyError("这个环境不允许写剪贴板，请手动选中文本复制");
      window.setTimeout(() => setCopyError(""), 3200);
      return;
    }
    setCopied(key);
    window.setTimeout(() => setCopied((current) => (current === key ? "" : current)), 1600);
  }

  let lastDay = "";

  return (
    <div className="temu-history-page">
      <header className="temu-history-head">
        <div className="temu-history-title">
          <h1>发布历史</h1>
          <p>成功与失败分开记；成功过的成品会自动从成品库移除，避免重复上架。</p>
        </div>
      </header>

      <div className="temu-history-toolbar">
        <div className="temu-history-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "success"}
            className={`temu-history-tab${tab === "success" ? " is-active" : ""}`}
            onClick={() => {
              setTab("success");
              setExpanded({});
            }}
          >
            成功 <em>{succeeded}</em>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "failed"}
            className={`temu-history-tab${tab === "failed" ? " is-active is-danger" : ""}`}
            onClick={() => {
              setTab("failed");
              setExpanded({});
            }}
          >
            失败 <em>{failed}</em>
          </button>
        </div>

        <label className="temu-history-search">
          <span className="temu-history-search-icon" aria-hidden="true">⌕</span>
          <input
            type="search"
            value={keyword}
            placeholder="搜标题、商品 id、类目名…"
            onChange={(event) => setKeyword(event.target.value)}
          />
          {keyword ? (
            <button type="button" className="temu-history-search-clear" onClick={() => setKeyword("")}>
              清空
            </button>
          ) : null}
        </label>
      </div>

      {error ? (
        <p className="temu-history-error" role="alert">
          {error}
        </p>
      ) : null}

      {copyError ? (
        <p className="temu-history-toast" role="status" aria-live="polite">
          {copyError}
        </p>
      ) : null}

      {!loading && !items.length ? <p className="temu-history-empty">{emptyText}</p> : null}

      <ul className="temu-history-list">
        {items.map((item) => {
          const isSuccess = item.status === "success";
          const code = errorCodeOf(item);
          const price = formatPriceRange(item.price_min, item.price_max);
          const source = sourceLabelOf(item.draft_id);
          const reason = item.error ? stripErrorCode(item.error, code) : "（Temu 没有返回原因）";
          const isLong = reason.length > 72;
          const isOpen = Boolean(expanded[item.id]);
          const day = dayLabelOf(item.created_ts, now);
          const showDay = day && day !== lastDay;
          if (showDay) lastDay = day;

          return (
            <li key={item.id} className="temu-history-group">
              {showDay ? (
                <p className="temu-history-day">
                  {day}
                  <span className="temu-history-day-count">{dayCounts.get(day) || 1} 条</span>
                </p>
              ) : null}
              <article className={`temu-history-row is-${item.status}`}>
                <HistoryThumb path={item.image} alt={item.title || "商品主图"} />

                <div className="temu-history-body">
                  <div className="temu-history-row-top">
                    <strong className="temu-history-name" title={item.title || undefined}>
                      {item.title || "（无标题）"}
                    </strong>
                    <span className={`temu-history-badge is-${item.status}`}>
                      {isSuccess ? "已发布" : "发布失败"}
                    </span>
                  </div>

                  <div className="temu-history-tags">
                    <span className="temu-history-tag is-source">{source}</span>
                    {item.cat_name ? (
                      <span className="temu-history-tag" title={item.cat_name}>
                        {item.cat_name}
                      </span>
                    ) : item.leaf_cat_id ? (
                      <span className="temu-history-tag">类目 {item.leaf_cat_id}</span>
                    ) : null}
                    {item.sku_count ? (
                      <span className="temu-history-tag">{item.sku_count} 个 SKU</span>
                    ) : null}
                    {price ? <span className="temu-history-tag is-price">{price}</span> : null}
                    {item.shop_name ? (
                      <span className="temu-history-tag is-shop" title={item.shop_name}>
                        {item.shop_name}
                      </span>
                    ) : null}
                  </div>

                  {isSuccess ? (
                    <div className="temu-history-row-foot">
                      <span className="temu-history-time" title={item.created_at}>
                        {formatRelativeTime(item.created_ts, now) || item.created_at}
                      </span>
                      <span className="temu-history-gid">
                        商品 id <b>{item.goods_id || "—"}</b>
                      </span>
                      {item.goods_id ? (
                        <button
                          type="button"
                          className="temu-history-copy"
                          onClick={() => void handleCopy(`gid-${item.id}`, item.goods_id)}
                        >
                          {copied === `gid-${item.id}` ? "已复制" : "复制 id"}
                        </button>
                      ) : null}
                    </div>
                  ) : (
                    <div className="temu-history-failure">
                      <div className="temu-history-failure-head">
                        <span className="temu-history-time" title={item.created_at}>
                          {formatRelativeTime(item.created_ts, now) || item.created_at}
                        </span>
                        {code ? <span className="temu-history-code">错误码 {code}</span> : null}
                      </div>
                      <p className={`temu-history-reason${isLong && !isOpen ? " is-clamped" : ""}`}>
                        {reason}
                      </p>
                      <div className="temu-history-row-foot">
                        {isLong ? (
                          <button
                            type="button"
                            className="temu-history-link"
                            onClick={() =>
                              setExpanded((current) => ({ ...current, [item.id]: !current[item.id] }))
                            }
                          >
                            {isOpen ? "收起原因" : "展开全文"}
                          </button>
                        ) : null}
                        <button
                          type="button"
                          className="temu-history-link"
                          // 复制的是原始文案（含错误码）：贴给客服/同事时码比徽章有用
                          onClick={() => void handleCopy(`err-${item.id}`, item.error || reason)}
                        >
                          {copied === `err-${item.id}` ? "已复制" : "复制原因"}
                        </button>
                        {onOpenDraftLibrary ? (
                          <button type="button" className="temu-history-retry" onClick={onOpenDraftLibrary}>
                            去成品库重发
                          </button>
                        ) : null}
                      </div>
                    </div>
                  )}
                </div>
              </article>
            </li>
          );
        })}
      </ul>

      {loading && !items.length ? <p className="temu-history-empty">加载中…</p> : null}

      {total > items.length ? (
        <div className="temu-history-more">
          <button
            type="button"
            className="temu-btn"
            disabled={loading}
            onClick={() => void load(tab, page + 1, true, appliedKeyword)}
          >
            {loading ? "加载中…" : `加载更多（还有 ${total - items.length} 条）`}
          </button>
        </div>
      ) : null}
    </div>
  );
}
