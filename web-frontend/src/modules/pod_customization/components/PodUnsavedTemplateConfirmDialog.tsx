import { useEffect, useRef } from "react";

type Props = {
  open: boolean;
  templateName: string;
  busy?: boolean;
  onClose: () => void;
  onConfirm: () => void;
};

export function PodUnsavedTemplateConfirmDialog({ open, templateName, busy = false, onClose, onConfirm }: Props) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    confirmRef.current?.focus();
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open, busy, onClose]);

  if (!open) return null;

  return (
    <div className="pod-unsaved-confirm-backdrop" role="presentation" onMouseDown={() => !busy && onClose()}>
      <section className="pod-unsaved-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="pod-unsaved-confirm-title" onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <div><span>UNSAVED TEMPLATE</span><h2 id="pod-unsaved-confirm-title">更换模板确认</h2></div>
          <button type="button" onClick={onClose} disabled={busy} aria-label="关闭确认弹窗">×</button>
        </header>
        <p className="pod-unsaved-confirm-message">
          当前模板<span className="pod-unsaved-confirm-name">{templateName || "（未命名）"}</span>暂未保存。
          更换模板会清空当前模板预设（业务信息、店小秘上架信息与 SKU），是否确认？
        </p>
        <footer>
          <button type="button" onClick={onClose} disabled={busy}>取消</button>
          <button type="button" className="pod-unsaved-confirm-confirm" ref={confirmRef} disabled={busy} onClick={onConfirm}>{busy ? "正在更换" : "确认更换并清空"}</button>
        </footer>
      </section>
    </div>
  );
}
