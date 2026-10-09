import { createPortal } from "react-dom";

import { CompositionPanelsEditor } from "./CompositionPanelsEditor";
import type { PodComposition } from "../types";

type Props = {
  open: boolean;
  onClose: () => void;
  /** 要编辑的模板；为空则不渲染。 */
  target: PodComposition | null;
  /** 保存成功后回调，供页面同步提示与生效状态。 */
  onChanged?: (composition: PodComposition) => void;
};

function displayName(template: PodComposition): string {
  const name = template.name.trim();
  if (name) return name;
  const raw = template.raw_input.trim();
  return raw ? (raw.length > 24 ? `${raw.slice(0, 24)}…` : raw) : "未命名构图";
}

/**
 * 「编辑构图模板」：从「构图模板管理」进入，**只做手动编辑**（不含 AI 生成）。
 */
export function CompositionEditDrawer({ open, onClose, target, onChanged }: Props) {
  if (!open || !target) return null;

  return createPortal(
    <div className="pod-composition-drawer-layer">
      <button type="button" className="pod-composition-drawer-backdrop" onClick={onClose} aria-label="关闭" />
      <aside className="pod-composition-drawer" role="dialog" aria-modal="true" aria-label="编辑构图模板">
        <header className="pod-composition-drawer-header">
          <div>
            <span>COMPOSITION TEMPLATE · 手动编辑</span>
            <h2>编辑构图模板</h2>
            <p>{displayName(target)}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">×</button>
        </header>

        <div className="pod-composition-drawer-body">
          {target.is_builtin ? (
            <section className="pod-composition-result" aria-label="画面指令">
              <p className="pod-composition-empty">系统内置「默认模板」不可编辑；如需自定义，请在左侧「构图定制」里生成一份。</p>
            </section>
          ) : (
            <CompositionPanelsEditor composition={target} onSaved={(saved) => onChanged?.(saved)} />
          )}
        </div>
      </aside>
    </div>,
    document.body,
  );
}
