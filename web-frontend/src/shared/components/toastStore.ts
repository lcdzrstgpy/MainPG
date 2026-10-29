export type ToastKind = "info" | "success" | "error";

/** 右上角最多同时堆叠 3 条，超出丢弃最旧的，避免连续操作把屏幕刷满。 */
const MAX_VISIBLE_TOASTS = 3;

/** 默认生命周期：5 秒后自动消失。 */
const DEFAULT_DURATION_MS = 5000;

export type ToastItem = {
  id: number;
  message: string;
  kind: ToastKind;
  durationMs: number;
};

type Listener = () => void;

let items: ToastItem[] = [];
let nextId = 1;
const listeners = new Set<Listener>();

function emit() {
  for (const listener of listeners) listener();
}

export function subscribeToasts(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getToasts(): ToastItem[] {
  return items;
}

export function dismissToast(id: number) {
  items = items.filter((toast) => toast.id !== id);
  emit();
}

/** 右上角全局提示：5 秒后自动消失，也可点击 × 立即关闭。 */
export function showToast(message: string, kind: ToastKind = "info", durationMs = DEFAULT_DURATION_MS) {
  const id = nextId++;
  items = [...items, { id, message, kind, durationMs }].slice(-MAX_VISIBLE_TOASTS);
  emit();
  window.setTimeout(() => dismissToast(id), durationMs);
}
