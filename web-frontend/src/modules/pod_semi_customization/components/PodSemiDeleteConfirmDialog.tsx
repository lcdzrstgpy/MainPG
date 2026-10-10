import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

type Props = {
  open: boolean;
  /** 将要删除的批次数（单删 = 1，批量删 = 勾选数）。 */
  count: number;
  busy?: boolean;
  onClose: () => void;
  onConfirm: () => void;
};

/**
 * 半定制删除的专用强确认弹窗（docs/POD-本地图片缓存与清理说明.md §6.3）。
 *
 * 半定制的图案**只保存在本机、没有图床公网备份**，删除 = 永久丢失。因此：
 * - 不能用一行 window.confirm 敷衍：文案必须写明「只保存在本机」「没有图床公网
 *   备份」「删除后不可恢复」，并引导先通过「下载 ZIP」导出；
 * - 确认按钮用危险色（红）且文案要表态（「我已导出，仍要删除」），不写「确定」；
 * - 默认焦点落在「取消」；点遮罩 / 按 Esc / 点 × 都等于取消；
 * - 删除进行中禁用按钮并给出进行中态；批量删除显示将要删除的批次数。
 */
export function PodSemiDeleteConfirmDialog({ open, count, busy = false, onClose, onConfirm }: Props) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    // §6.3：默认焦点必须落在「取消」，不要落在危险按钮上。
    cancelRef.current?.focus();
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open, busy, onClose]);

  if (!open) return null;

  const batchCount = Math.max(1, Number(count) || 1);

  // portal 到 body：tab 面板入场动画的层叠上下文会把 fixed 层压到顶栏下面。
  return createPortal(
    <div
      className="pod-semi-delete-confirm-backdrop"
      role="presentation"
      onMouseDown={() => {
        if (!busy) onClose();
      }}
    >
      <section
        className="pod-semi-delete-confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="pod-semi-delete-confirm-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span>DELETE SEMI BATCH</span>
            <h2 id="pod-semi-delete-confirm-title">删除半定制批次</h2>
          </div>
          <button type="button" onClick={onClose} disabled={busy} aria-label="关闭确认弹窗">
            ×
          </button>
        </header>
        <div className="pod-semi-delete-confirm-body">
          {batchCount > 1 && (
            <strong className="pod-semi-delete-confirm-count">本次将删除 {batchCount} 个批次。</strong>
          )}
          <p>
            半定制的图案<strong>只保存在本机，没有图床公网备份</strong>。
            {batchCount > 1 ? "删除这些批次" : "删除该批次"}会把本地图片一并清理，
            <strong>删除后不可恢复</strong>。
          </p>
          <p>请先确认需要的图案已通过「下载 ZIP」导出到本地，再执行删除。</p>
        </div>
        <footer>
          <button type="button" ref={cancelRef} onClick={onClose} disabled={busy}>
            取消
          </button>
          <button
            type="button"
            className="pod-semi-delete-confirm-danger"
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? "正在删除…" : "我已导出，仍要删除"}
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}
