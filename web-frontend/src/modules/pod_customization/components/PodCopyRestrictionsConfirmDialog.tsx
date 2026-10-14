import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

type Props = {
  open: boolean;
  /** 用户坚持填写：保留已输入内容，本次编辑期间不再重复提示。 */
  onKeep: () => void;
  /** 用户取消：回退本次输入（通常是清空）。 */
  onCancel: () => void;
};

/**
 * 「标题/描述限制」填写前的强提示。
 *
 * 该字段要求 AI 在标题/描述里强制带上（或禁止）某个说法，极易触发平台标题风控，
 * 因此只在用户真的往里输入内容时弹一次，默认引导留空。
 */
export function PodCopyRestrictionsConfirmDialog({ open, onKeep, onCancel }: Props) {
  const keepRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", closeOnEscape);
    // preventScroll：弹窗是 fixed 居中的，抢焦点时不要让浏览器把页面滚一下（会表现为画面跳动）。
    keepRef.current?.focus({ preventScroll: true });
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open, onCancel]);

  if (!open) return null;

  // portal 到 body：tab 面板动画的层叠上下文会锁住 fixed 层 z-index，被顶栏盖住
  return createPortal(
    <div className="pod-unsaved-confirm-backdrop" role="presentation" onMouseDown={onCancel}>
      <section
        className="pod-unsaved-confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pod-copy-restrictions-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div><span>COPY RESTRICTIONS</span><h2 id="pod-copy-restrictions-title">确认填写「标题/描述限制」</h2></div>
          <button type="button" onClick={onCancel} aria-label="关闭确认弹窗">×</button>
        </header>
        <p className="pod-copy-restrictions-warning" role="alert">
          该提示仅用于极少数需要在标题强制说明的特定品类特点，请谨慎填写，同时极易触发平台标题风控，默认建议留空！！！
        </p>
        <footer>
          <button type="button" onClick={onCancel}>取消</button>
          <button type="button" className="pod-unsaved-confirm-confirm" ref={keepRef} onClick={onKeep}>依旧填写</button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}
