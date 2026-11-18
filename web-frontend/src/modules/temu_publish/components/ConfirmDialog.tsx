import { useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * 应用内确认弹窗（替代 window.confirm）。
 *
 * 原生框三个毛病：样式跟工作台不搭；WebView 宿主可能禁用（confirm 直接返回 false，
 * 功能静默失效）；无头自动化里没人点会把渲染进程永久卡死。所以统一走这个。
 *
 * 用法：
 * ```tsx
 * const [confirmNode, confirm] = useAppConfirm();
 * …
 * if (!(await confirm({ title: "删除店铺？", message: "删掉之后要重新贴 token。" }))) return;
 * …
 * return <>{confirmNode}<页面其余部分 /></>;
 * ```
 */
export type ConfirmRequest = {
  title: string;
  /** 会按换行分段显示 */
  message: string;
  confirmText?: string;
  cancelText?: string;
  /** 破坏性操作把确认按钮染成警示色 */
  danger?: boolean;
};

type Pending = { request: ConfirmRequest; resolve: (value: boolean) => void };

export function useAppConfirm(): [ReactNode, (request: ConfirmRequest) => Promise<boolean>] {
  const [pending, setPending] = useState<Pending | null>(null);
  /** 当前挂起的 resolver：新请求顶掉旧请求时必须先放行旧的（按取消处理），
   * 否则旧 Promise 永不结算，await 它的 run() 会把 busy 状态卡死。 */
  const resolverRef = useRef<((value: boolean) => void) | null>(null);

  const confirm = (request: ConfirmRequest) =>
    new Promise<boolean>((resolve) => {
      resolverRef.current?.(false);
      resolverRef.current = resolve;
      setPending({ request, resolve });
    });

  const settle = (value: boolean) => {
    resolverRef.current?.(value);
    resolverRef.current = null;
    setPending(null);
  };

  const node = pending
    ? createPortal(
        <div className="temu-edit-backdrop" onClick={() => settle(false)}>
          <div
            className="temu-edit-dialog temu-confirm-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={pending.request.title}
            onClick={(event) => event.stopPropagation()}
          >
            <header className="temu-edit-head">
              <div>
                <strong>{pending.request.title}</strong>
              </div>
              <button type="button" onClick={() => settle(false)} aria-label="关闭">
                ×
              </button>
            </header>
            <div className="temu-edit-body">
              <p className="temu-confirm-text">
                {pending.request.message.split("\n").map((line, index) => (
                  <span key={index}>
                    {line}
                    <br />
                  </span>
                ))}
              </p>
            </div>
            <footer className="temu-edit-foot">
              <button type="button" className="temu-btn" onClick={() => settle(false)}>
                {pending.request.cancelText ?? "取消"}
              </button>
              <button
                type="button"
                className={`temu-btn ${pending.request.danger ? "temu-btn-danger" : "temu-btn-primary"}`}
                onClick={() => settle(true)}
              >
                {pending.request.confirmText ?? "确认"}
              </button>
            </footer>
          </div>
        </div>,
        document.body,
      )
    : null;

  return [node, confirm];
}
