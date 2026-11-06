import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { httpBlob } from "../../../transport/http/client";

/** 公网地址直接用；本地媒体（/api/product-processing/media/...）要带鉴权取 blob。 */
function isPublicHttp(url: string): boolean {
  if (!/^https?:\/\//i.test(url)) return false;
  // 指回本地后端的绝对地址同样不能直接丢给 <img>：它带不了 Authorization，
  // 而地址里的 signature 两小时就过期 —— 过期后 <img> 永远加载不出来。
  return !/^https?:\/\/(127\.0\.0\.1|localhost)(:|\/|$)/i.test(url);
}

export function TemuThumb({ path, onOpen }: { path: string; onOpen: (src: string) => void }) {
  const [src, setSrc] = useState("");
  /**
   * 读文件失败（404 / 素材文件已被清掉）。
   *
   * 以前失败也只是 `setSrc("")`，界面继续显示「…」——用户会以为在加载，
   * 一直等下去；实际这张图永远出不来，提交时才报「图片读不到」。
   */
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    let objectUrl = "";
    setSrc("");
    setFailed(false);
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
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);

  // 没有图（比如变种没单独设预览图）就给个占位框，别把加载中的「…」当成空图
  if (!path)
    return (
      <span className="temu-img-empty" title="暂无图片">
        无图
      </span>
    );
  if (failed) {
    return (
      <span className="temu-img-failed" title={`这张图读不到，文件可能已经不在：${path}`}>
        失效
      </span>
    );
  }
  if (!src) return <span className="temu-img-strip-loading">…</span>;
  return (
    <button type="button" className="temu-img-thumb" onClick={() => onOpen(src)} title="点击放大">
      {/* referrerPolicy=no-referrer：alicdn（1688 图床）校验 Referer，带
          http://127.0.0.1:5173 会被直接 403，缩略图就永远是空白。
          顺带 onError 落到「失效」，不要留一个裂图。 */}
      <img
        src={src}
        alt=""
        loading="lazy"
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
      />
    </button>
  );
}

/**
 * 上传位：空槽位时是一整块虚线区域，有图时是条带末尾的一枚小虚线格。
 *
 * 以前的空态只有一行灰字（「草稿带入的图会显示在这里…」），既看不出这里能放图，
 * 也不知道能拖进来 —— 用户得先去点右上角那个小按钮。上传是发布流程里最核心的动作，
 * 必须有一个看得见、点得到、能拖能粘的落点。
 */
function ImageAddTile({
  onAddFiles,
  hint,
  subHint,
  compact,
  disabled,
}: {
  onAddFiles: (files: File[]) => void;
  hint: string;
  subHint?: string;
  compact?: boolean;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [over, setOver] = useState(false);

  const take = (files: FileList | null) => {
    if (disabled || !files) return;
    const picked = Array.from(files).filter((file) => file.type.startsWith("image/"));
    if (picked.length) onAddFiles(picked);
  };

  return (
    <div
      className={`temu-img-add${compact ? " is-compact" : ""}${over ? " is-over" : ""}${
        disabled ? " is-disabled" : ""
      }`}
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-label={hint}
      aria-disabled={disabled ? true : undefined}
      onClick={() => {
        if (!disabled) inputRef.current?.click();
      }}
      onKeyDown={(event) => {
        if (disabled) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          inputRef.current?.click();
        }
      }}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        take(event.dataTransfer?.files ?? null);
      }}
      onPaste={(event) => {
        // 截图后直接 Ctrl+V：只有在获得焦点时才会走到这里，所以给了 tabIndex
        const files = Array.from(event.clipboardData?.files ?? []);
        if (!files.length) return;
        event.preventDefault();
        take(event.clipboardData.files);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        multiple
        hidden
        disabled={disabled}
        onChange={(event) => {
          take(event.target.files);
          event.target.value = "";
        }}
      />
      <span className="temu-img-add-plus" aria-hidden="true">
        ＋
      </span>
      {compact ? null : (
        <>
          <span className="temu-img-add-text">{disabled ? "已达上限" : hint}</span>
          {subHint ? <span className="temu-img-add-sub">{subHint}</span> : null}
        </>
      )}
    </div>
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
  firstBadge,
  onAddFiles,
  addHint,
  addSubHint,
  maxCount,
  busy,
  variant = "primary",
}: {
  urls: string[];
  onChange: (next: string[]) => void;
  emptyHint?: string;
  /** 第 1 张上的角标。只有轮播图的第一张是「主图」，详情图/外包装图没有这个概念。 */
  firstBadge?: string;
  /** 传了才显示上传位；不传就退化成原来的纯提示文案。 */
  onAddFiles?: (files: File[]) => void;
  addHint?: string;
  addSubHint?: string;
  /** 张数上限：到顶后上传位变成「已达上限」，不再可点。 */
  maxCount?: number;
  busy?: boolean;
  /**
   * primary：必填/主图槽位，空态铺满整行；
   * compact：可选槽位，空态只是左侧一枚 88×88 的小落点 + 右侧一行说明 ——
   * 三个同样大的虚线框堆在一起，页面会被撑得很长、也分不出主次。
   */
  variant?: "primary" | "compact";
}) {
  const [preview, setPreview] = useState<string | null>(null);
  const list = urls.map((item) => item.trim()).filter(Boolean);
  const atLimit = typeof maxCount === "number" && list.length >= maxCount;
  const uploadTile = onAddFiles ? (
    <ImageAddTile
      onAddFiles={onAddFiles}
      hint={addHint ?? "点击上传，或把图片拖进来"}
      subHint={addSubHint}
      compact={list.length > 0 || variant === "compact"}
      disabled={atLimit || busy}
    />
  ) : null;

  if (list.length === 0) {
    if (!uploadTile) return emptyHint ? <p className="temu-img-strip-hint">{emptyHint}</p> : null;
    // 可选槽位：小落点 + 右侧说明，横向占一行就够
    if (variant === "compact") {
      return (
        <div className="temu-img-add-row">
          {uploadTile}
          <span className="temu-img-add-note">{addSubHint || addHint}</span>
        </div>
      );
    }
    return uploadTile;
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
            {index === 0 && firstBadge ? <em className="temu-img-main">{firstBadge}</em> : null}
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
        {uploadTile}
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
            <img
              src={preview}
              alt=""
              referrerPolicy="no-referrer"
              onClick={(event) => event.stopPropagation()}
            />
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
