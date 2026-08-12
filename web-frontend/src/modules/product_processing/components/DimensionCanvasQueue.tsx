import { useRef, type WheelEvent } from "react";

import type { DimensionCanvasItem } from "../types/dimensionCanvas";

type Props = {
  items: DimensionCanvasItem[];
  activeItemId: string;
  onSelect: (itemId: string) => void;
  onPrevious: () => void;
  onNext: () => void;
  onRetryRender: (itemId: string) => void;
};

const STATE_LABELS: Record<string, string> = {
  pending: "待处理",
  editing: "编辑中",
  needs_dimensions: "待补尺寸",
  asset_failed: "素材失败",
  rendering: "渲染中",
  render_retryable: "可重试",
  completed: "已完成",
  submitted: "已交回",
  conflict: "回写冲突",
  accepted: "已接受",
  skipped: "已跳过",
};

export function DimensionCanvasQueue({ items, activeItemId, onSelect, onPrevious, onNext, onRetryRender }: Props) {
  const lastWheelAt = useRef(0);

  const handleWheel = (event: WheelEvent<HTMLElement>) => {
    if (Math.abs(event.deltaY) < 12) return;
    event.preventDefault();
    const now = Date.now();
    if (now - lastWheelAt.current < 220) return;
    lastWheelAt.current = now;
    if (event.deltaY > 0) onNext();
    else onPrevious();
  };

  const activeIndex = Math.max(0, items.findIndex((item) => item.id === activeItemId));
  return (
    <aside className="dimension-queue" onWheel={handleWheel} aria-label="商品队列">
      <div className="dimension-queue-head">
        <div>
          <strong>商品队列</strong>
          <span>{items.length ? activeIndex + 1 : 0} / {items.length}</span>
        </div>
        <div className="dimension-queue-nav">
          <button onClick={onPrevious} disabled={activeIndex <= 0}>上一条</button>
          <button onClick={onNext} disabled={activeIndex >= items.length - 1}>下一条</button>
        </div>
      </div>
      <div className="dimension-queue-list">
        {items.map((item) => (
          <button
            key={item.id}
            className={`dimension-queue-item${item.id === activeItemId ? " is-active" : ""}`}
            onClick={() => onSelect(item.id)}
          >
            <span className={`dimension-state-dot state-${item.state}`} aria-hidden="true" />
            <span className="dimension-queue-copy">
              <strong>{item.skc || `商品 #${item.productDraftId}`}</strong>
              <small>{STATE_LABELS[item.state] ?? item.state}</small>
            </span>
            {item.state === "render_retryable" && (
              <span
                className="dimension-inline-action"
                role="button"
                tabIndex={0}
                onClick={(event) => { event.stopPropagation(); onRetryRender(item.id); }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.stopPropagation();
                    onRetryRender(item.id);
                  }
                }}
              >
                重试
              </span>
            )}
          </button>
        ))}
      </div>
    </aside>
  );
}
