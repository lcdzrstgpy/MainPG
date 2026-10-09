export type ToastKind = "info" | "success" | "error";

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

/** 顶部全局提示：几秒后自动消失，也可点击 × 立即关闭。 */
export function showToast(message: string, kind: ToastKind = "info", durationMs = 4200) {
  const id = nextId++;
  items = [...items, { id, message, kind, durationMs }];
  emit();
  window.setTimeout(() => dismissToast(id), durationMs);
}
