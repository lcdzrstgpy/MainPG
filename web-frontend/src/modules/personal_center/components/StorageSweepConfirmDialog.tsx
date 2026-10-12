import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

export type SweepConfirmItem = {
  key: string;
  label: string;
  files: number;
  bytes: number;
};

type Props = {
  open: boolean;
  /** 勾选的可清理项（已过滤 files > 0），弹窗打开时冻结快照。 */
  items: SweepConfirmItem[];
  onClose: () => void;
  onConfirm: () => void;
};

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 MB";
  return `${(bytes / 1024 / 1024).toFixed(bytes >= 10 * 1024 * 1024 ? 1 : 2)} MB`;
}

/**
 * 「本机唯一副本、删除后不可恢复」的分类——确认按钮必须用危险色并给醒目警示。
 * 「历史任务的复核原图」没有云端备份；其余分类要么有图床链接兜底、要么本就
 * 是垃圾文件，风险不同，必须区分开。
 */
const UNRECOVERABLE_KEYS = new Set(["pp_review_images"]);

/**
 * 「存储空间」清理的删除确认弹窗。
 *
 * 清理虽不像「删除批次」那样整体不可逆，但一旦执行就无法在界面里「撤销」，
 * 且部分分类（复核原图）删除后彻底无法找回——所以必须先列清单、再确认：
 * - 列出每个勾选分类的名称、文件数与体积，以及总计；
 * - 勾选含不可恢复分类时给红色警示，确认按钮切换为危险色；
 * - 默认焦点落在「取消」；点遮罩 / 按 Esc / 点 × 都等于取消。
 */
export function StorageSweepConfirmDialog({ open, items, onClose, onConfirm }: Props) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    // 默认焦点落在「取消」，避免误触确认。
    cancelRef.current?.focus();
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open, onClose]);

  if (!open) return null;

  const totalFiles = items.reduce((sum, item) => sum + item.files, 0);
  const totalBytes = items.reduce((sum, item) => sum + item.bytes, 0);
  const hasUnrecoverable = items.some((item) => UNRECOVERABLE_KEYS.has(item.key));

  // portal 到 body：tab 面板入场动画的层叠上下文会把 fixed 层压到顶栏下面。
  return createPortal(
    <div
      className="storage-sweep-confirm-backdrop"
      role="presentation"
      onMouseDown={onClose}
    >
      <section
        className="storage-sweep-confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="storage-sweep-confirm-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span>CLEAN LOCAL CACHE</span>
            <h2 id="storage-sweep-confirm-title">确认清理选中项</h2>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭确认弹窗">
            ×
          </button>
        </header>
        <div className="storage-sweep-confirm-body">
          <p>
            将清理以下 {items.length} 类本地缓存文件，共 {totalFiles} 个 ·{" "}
            {formatBytes(totalBytes)}：
          </p>
          <ul className="storage-sweep-confirm-list">
            {items.map((item) => (
              <li key={item.key}>
                <span>{item.label}</span>
                <em>
                  {item.files} 个 · {formatBytes(item.bytes)}
                </em>
              </li>
            ))}
          </ul>
          {hasUnrecoverable && (
            <p className="storage-sweep-confirm-warning">
              「历史任务的复核原图」没有云端备份，删除后无法找回。
            </p>
          )}
          <p className="storage-sweep-confirm-note">
            清理只删除本机缓存文件；使用中的图片、模板与任务记录不会被删除，已发布到图床的图片仍可在线查看。
          </p>
        </div>
        <footer>
          <button type="button" ref={cancelRef} onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className={
              hasUnrecoverable ? "storage-sweep-confirm-danger" : "storage-sweep-confirm-primary"
            }
            onClick={onConfirm}
          >
            确认清理
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}
