import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { httpBlob } from "../../../transport/http/client";

/** 公网地址直接用；本地媒体（/api/product-processing/media/...）要带鉴权取 blob。 */
function isPublicHttp(url: string): boolean {
  return /^https?:\/\//i.test(url);
}

export function TemuThumb({ path, onOpen }: { path: string; onOpen: (src: string) => void }) {
  const [src, setSrc] = useState("");

  useEffect(() => {
    let alive = true;
    let objectUrl = "";
    setSrc("");
    if (!path) return;
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
        if (alive) setSrc("");
      });
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);

  // 没有图（比如变种没单独设预览图）就给个占位框，别把加载中的「…」当成空图
  if (!path) return <span className="temu-img-empty" title="暂无图片" />;
  if (!src) return <span className="temu-img-strip-loading">…</span>;
  return (
    <button type="button" className="temu-img-thumb" onClick={() => onOpen(src)} title="点击放大">
      <img src={src} alt="" loading="lazy" />
    </button>
  );
}

/**
 * 图片清单预览条。
 *
 * 草稿库「用于发布」带过来的图是本地媒体地址（`/api/product-processing/media/...`），
 * 只显示成一行 URL 根本看不出是什么图。这里把它们渲染成缩略图，并支持调序 / 删除。
 * 第一张就是 Temu 的轮播主图（也是素材图），所以标「主图」并禁止前移。
 */
export function TemuImageStrip({
  urls,
  onChange,
  emptyHint,
}: {
  urls: string[];
  onChange: (next: string[]) => void;
  emptyHint?: string;
}) {
  const [preview, setPreview] = useState<string | null>(null);
  const list = urls.map((item) => item.trim()).filter(Boolean);

  if (list.length === 0) {
    return emptyHint ? <p className="temu-img-strip-hint">{emptyHint}</p> : null;
  }

  const swap = (from: number, to: number) => {
    if (to < 0 || to >= list.length) return;
    const next = [...list];
    [next[from], next[to]] = [next[to], next[from]];
    onChange(next);
  };

  return (
    <>
      <div className="temu-img-strip">
        {list.map((url, index) => (
          <span key={`${url}-${index}`} className="temu-img-item">
            <TemuThumb path={url} onOpen={setPreview} />
            {index === 0 && <em className="temu-img-main">主图</em>}
            <span className="temu-img-ops">
              <button
                type="button"
                onClick={() => swap(index, index - 1)}
                disabled={index === 0}
                title="前移"
              >
                ←
              </button>
              <button
                type="button"
                onClick={() => swap(index, index + 1)}
                disabled={index === list.length - 1}
                title="后移"
              >
                →
              </button>
              <button
                type="button"
                onClick={() => onChange(list.filter((_, i) => i !== index))}
                title="移除这张"
              >
                ×
              </button>
            </span>
          </span>
        ))}
        <span className="temu-img-count">共 {list.length} 张</span>
      </div>

      {preview &&
        createPortal(
          <div
            className="temu-image-preview"
            role="dialog"
            aria-modal="true"
            aria-label="图片预览"
            onClick={() => setPreview(null)}
          >
            <img src={preview} alt="" onClick={(event) => event.stopPropagation()} />
            <button
              type="button"
              className="temu-image-preview-close"
              onClick={() => setPreview(null)}
              aria-label="关闭预览"
            >
              ×
            </button>
          </div>,
          document.body,
        )}
    </>
  );
}
