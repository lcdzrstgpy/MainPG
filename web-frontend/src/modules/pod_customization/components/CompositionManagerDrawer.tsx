import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { podCustomizationApi } from "../api/podCustomizationApi";
import type { PodComposition } from "../types";

type Props = {
  open: boolean;
  onClose: () => void;
  /** 点「编辑」时把该模板交给页面打开编辑抽屉。 */
  onEdit: (composition: PodComposition) => void;
  /** 生效模板发生变化（设为生效 / 删除）时回调，供页面同步提示。 */
  onActiveChanged?: (composition: PodComposition | null) => void;
};

function displayName(template: PodComposition): string {
  const name = template.name.trim();
  if (name) return name;
  const raw = template.raw_input.trim();
  return raw ? (raw.length > 24 ? `${raw.slice(0, 24)}…` : raw) : "未命名构图";
}

function timeLabel(updatedAt: string): string {
  const parsed = Date.parse(updatedAt);
  if (!Number.isFinite(parsed)) return updatedAt;
  return new Date(parsed).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function CompositionManagerDrawer({ open, onClose, onEdit, onActiveChanged }: Props) {
  const [templates, setTemplates] = useState<PodComposition[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      const response = await podCustomizationApi.listCompositions();
      setTemplates(response.templates);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    setNotice("");
    void refresh();
  }, [open]);

  const notifyActive = (items: PodComposition[]) => {
    onActiveChanged?.(items.find((item) => item.is_active) ?? null);
  };

  const activate = async (template: PodComposition) => {
    if (busyId) return;
    setBusyId(template.composition_id);
    setError("");
    setNotice("");
    try {
      await podCustomizationApi.activateComposition(template.composition_id);
      const response = await podCustomizationApi.listCompositions();
      setTemplates(response.templates);
      notifyActive(response.templates);
      setNotice(`已把「${displayName(template)}」设为生效，之后新建批次会用它。`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyId("");
    }
  };

  const rename = async (template: PodComposition) => {
    if (busyId) return;
    const next = window.prompt("模板名称", displayName(template));
    if (next === null) return;
    const name = next.trim();
    if (!name) return;
    setBusyId(template.composition_id);
    setError("");
    setNotice("");
    try {
      const saved = await podCustomizationApi.renameComposition(template.composition_id, name);
      setTemplates((current) => current.map((item) => (item.composition_id === saved.composition_id ? saved : item)));
      setNotice("已重命名。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyId("");
    }
  };

  const remove = async (template: PodComposition) => {
    if (busyId) return;
    if (!window.confirm(`确认删除构图模板「${displayName(template)}」？删除后不可恢复。`)) return;
    setBusyId(template.composition_id);
    setError("");
    setNotice("");
    try {
      await podCustomizationApi.deleteComposition(template.composition_id);
      const response = await podCustomizationApi.listCompositions();
      setTemplates(response.templates);
      notifyActive(response.templates);
      setNotice("已删除该模板。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyId("");
    }
  };

  if (!open) return null;

  return createPortal(
    <div className="pod-composition-manager-layer">
      <button type="button" className="pod-composition-manager-backdrop" onClick={onClose} aria-label="关闭" />
      <aside className="pod-composition-manager" role="dialog" aria-modal="true" aria-label="构图模板管理">
        <header className="pod-composition-manager-header">
          <div>
            <span>COMPOSITION TEMPLATES</span>
            <h2>构图模板管理</h2>
            <p>系统内置「默认模板」可直接选用；新建批次用「生效中」的那一份，未新建时回退默认模板。</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">×</button>
        </header>

        <div className="pod-composition-manager-body">
          {error && <p className="pod-composition-error" role="alert">{error}</p>}
          {!error && notice && <p className="pod-composition-notice" role="status">{notice}</p>}

          {loading && templates.length === 0 && <p className="pod-composition-empty">正在加载…</p>}

          <ul className="pod-composition-manager-list">
            {templates.map((template) => (
              <li key={template.composition_id} className={template.is_active ? "is-active" : undefined}>
                <div className="pod-composition-manager-meta">
                  <b>{displayName(template)}</b>
                  {template.is_builtin && <span className="pod-composition-manager-tag">系统内置</span>}
                  {template.is_active && <span className="pod-composition-manager-badge">生效中</span>}
                  {!template.is_builtin && <small>{timeLabel(template.updated_at)}</small>}
                </div>
                <p className="pod-composition-manager-preview">{template.panels.panel_1.zh || "（尚无画面指令）"}</p>
                <div className="pod-composition-manager-actions">
                  <button type="button" disabled={template.is_active || busyId === template.composition_id} onClick={() => void activate(template)}>设为生效</button>
                  {!template.is_builtin && (
                    <>
                      <button type="button" disabled={busyId === template.composition_id} onClick={() => onEdit(template)}>编辑</button>
                      <button type="button" disabled={busyId === template.composition_id} onClick={() => void rename(template)}>重命名</button>
                      <button type="button" className="is-danger" disabled={busyId === template.composition_id} onClick={() => void remove(template)}>删除</button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </div>,
    document.body,
  );
}
