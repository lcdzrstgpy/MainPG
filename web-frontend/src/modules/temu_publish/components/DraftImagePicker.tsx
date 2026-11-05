import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import { temuPublishApi } from "../api/temuPublishApi";
import type { TemuDraftEntry } from "../types";

type Source = "all" | "ai" | "pod";

/**
 * 从「AI 处理 / POD 成品库」挑图。
 *
 * 背景：草稿库里的成品图本来就在，但发布页只能「本地上传」或手填 URL ——
 * 用户想复用同一款的其他图（或把详情图补到轮播）就得先存到本地再传一遍。
 * 店小秘那套「AI 商品套图」干的就是这件事：直接从已有成品里挑。
 *
 * 挑中的是**图片地址**（本地媒体路径或公网 URL 都行），提交时统一由
 * `resolveImage` 转存到 Temu 图床，不会把 127.0.0.1 的地址发出去。
 */
export function DraftImagePicker({
  slotLabel,
  existing,
  limit,
  onClose,
  onPick,
}: {
  /** 「轮播图 / 详情图 / 外包装图」——只用于文案 */
  slotLabel: string;
  /** 该槽位已有的图，挑图时置灰显示「已在列表里」 */
  existing: string[];
  /** 该槽位最多几张 */
  limit: number;
  onClose: () => void;
  onPick: (urls: string[]) => void;
}) {
  const [entries, setEntries] = useState<TemuDraftEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [source, setSource] = useState<Source>("all");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string[]>([]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    void temuPublishApi
      .draftLibrary({ source: source === "all" ? "" : source, q: query, page: 1, pageSize: 60 })
      .then((data) => {
        if (alive) setEntries(data.entries ?? []);
      })
      .catch((cause) => {
        if (alive) setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [source, query]);

  const owned = useMemo(() => new Set(existing), [existing]);
  const remaining = Math.max(0, limit - existing.length);

  const toggle = (url: string) => {
    setSelected((current) => {
      if (current.includes(url)) return current.filter((item) => item !== url);
      if (current.length >= remaining) return current;
      return [...current, url];
    });
  };

  const groups = entries
    .map((entry) => ({
      entry,
      images: Array.from(
        new Set([...(entry.carousel_images ?? []), ...(entry.images ?? [])].filter(Boolean)),
      ),
    }))
    .filter((group) => group.images.length > 0);

  return createPortal(
    <div className="temu-edit-backdrop" onClick={onClose}>
      <div
        className="temu-edit-dialog temu-picker-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="从成品库挑图"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="temu-edit-head">
          <div>
            <strong>从成品库挑图</strong>
            <span>
              选中的图会加到「{slotLabel}」· 还能放 {remaining} 张
            </span>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        <div className="temu-picker-toolbar">
          <div className="temu-picker-tabs">
            {(
              [
                ["all", "全部"],
                ["ai", "AI 处理"],
                ["pod", "POD 定制"],
              ] as Array<[Source, string]>
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={`temu-picker-tab${source === key ? " is-active" : ""}`}
                onClick={() => setSource(key)}
              >
                {label}
              </button>
            ))}
          </div>
          <input
            className="temu-input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜标题 / 货号"
            aria-label="搜索成品"
          />
        </div>

        {error ? (
          <p className="temu-picker-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="temu-picker-body">
          {loading ? (
            <p className="temu-empty">正在读取成品库…</p>
          ) : groups.length === 0 ? (
            <p className="temu-empty">
              没有可选的成品图。先去「产品处理」跑一批 AI 处理，或「POD 定制」生成一批。
            </p>
          ) : (
            groups.map(({ entry, images }) => (
              <section className="temu-picker-group" key={entry.id}>
                <header>
                  <strong title={entry.title}>{entry.title || entry.subtitle || "（无标题）"}</strong>
                  <span>
                    {entry.source === "ai" ? "AI 处理" : "POD"} · {images.length} 张
                  </span>
                </header>
                <div className="temu-picker-grid">
                  {images.slice(0, 12).map((url) => {
                    const picked = selected.includes(url);
                    const added = owned.has(url);
                    return (
                      <button
                        key={url}
                        type="button"
                        className={`temu-picker-thumb${picked ? " is-picked" : ""}${added ? " is-added" : ""}`}
                        title={added ? "这张已经在当前槽位里" : picked ? "点击取消" : "点击选中"}
                        disabled={added || (!picked && selected.length >= remaining)}
                        onClick={() => toggle(url)}
                      >
                        <img src={url} alt="" loading="lazy" referrerPolicy="no-referrer" />
                        {added ? <em>已在列表</em> : picked ? <em>已选</em> : null}
                      </button>
                    );
                  })}
                </div>
              </section>
            ))
          )}
        </div>

        <footer className="temu-edit-foot">
          <span className="temu-muted">已选 {selected.length} 张</span>
          <button type="button" className="temu-btn" onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className="temu-btn temu-btn-primary"
            disabled={!selected.length}
            onClick={() => onPick(selected)}
          >
            加入{slotLabel}（{selected.length}）
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
