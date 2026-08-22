import { useEffect, useState } from "react";

import { dismissToast, getToasts, subscribeToasts, type ToastItem } from "./toastStore";
import "./globalToast.css";

export function GlobalToast() {
  const [toasts, setToasts] = useState<ToastItem[]>(getToasts());

  useEffect(() => subscribeToasts(() => setToasts(getToasts())), []);

  if (!toasts.length) return null;

  return (
    <div className="global-toast-viewport" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`global-toast is-${toast.kind}`}>
          <span className="global-toast-icon" aria-hidden="true">
            {toast.kind === "error" ? "!" : toast.kind === "success" ? "✓" : "ℹ"}
          </span>
          <p>{toast.message}</p>
          <button type="button" onClick={() => dismissToast(toast.id)} aria-label="关闭提示">
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
