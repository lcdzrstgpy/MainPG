import { useEffect, useMemo, useState } from "react";

import { podCustomizationApi } from "../api/podCustomizationApi";
import { AutoGrowTextarea } from "./AutoGrowTextarea";
import type { PodComposition, PodCompositionPanelsZh } from "../types";

/** 一格中文指令的字符上限（与后端契约一致）。 */
export const COMPOSITION_PANEL_MAX_LENGTH = 500;

// 角色由我们固定；面向用户只展示角色（主图/细节图A/细节图B/素材图）。
export const COMPOSITION_PANEL_SLOTS: Array<{ key: keyof PodCompositionPanelsZh; label: string }> = [
  { key: "panel_1", label: "主图" },
  { key: "panel_2", label: "细节图 A" },
  { key: "panel_3", label: "细节图 B" },
  { key: "panel_4", label: "素材图" },
];

function draftsFrom(composition: PodComposition): PodCompositionPanelsZh {
  return {
    panel_1: composition.panels.panel_1.zh,
    panel_2: composition.panels.panel_2.zh,
    panel_3: composition.panels.panel_3.zh,
    panel_4: composition.panels.panel_4.zh,
  };
}

function timeLabel(updatedAt: string): string {
  const parsed = Date.parse(updatedAt);
  if (!Number.isFinite(parsed)) return "";
  return new Date(parsed).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type Props = {
  /** 要编辑的模板。 */
  composition: PodComposition;
  /** 外部忙状态（如生成中）时禁用编辑。 */
  disabled?: boolean;
  /** 保存成功回调（返回后端最新记录）。 */
  onSaved?: (composition: PodComposition) => void;
};

/**
 * 各角色画面指令编辑器（只做手动编辑）：改的是这一份原记录，保存时后台按中文重新转写英文。
 * 「构图定制」（AI 生成）与「编辑构图模板」（管理入口）共用本组件。
 */
export function CompositionPanelsEditor({ composition, disabled = false, onSaved }: Props) {
  const [drafts, setDrafts] = useState<PodCompositionPanelsZh>(() => draftsFrom(composition));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const busy = disabled || saving;

  // 切换到另一份模板（或保存后刷新）时同步草稿。
  useEffect(() => {
    setDrafts(draftsFrom(composition));
    setError("");
    setNotice("");
  }, [composition.composition_id, composition.updated_at]);

  const dirty = useMemo(
    () => COMPOSITION_PANEL_SLOTS.some((slot) => drafts[slot.key].trim() !== composition.panels[slot.key].zh.trim()),
    [drafts, composition],
  );
  const allFilled = COMPOSITION_PANEL_SLOTS.every((slot) => drafts[slot.key].trim().length > 0);

  const save = async () => {
    if (busy || !allFilled) return;
    setError("");
    setNotice("");
    setSaving(true);
    try {
      const panels: PodCompositionPanelsZh = {
        panel_1: drafts.panel_1.trim(),
        panel_2: drafts.panel_2.trim(),
        panel_3: drafts.panel_3.trim(),
        panel_4: drafts.panel_4.trim(),
      };
      const saved = await podCustomizationApi.updateComposition(composition.composition_id, { panels });
      setDrafts(draftsFrom(saved));
      setNotice("已保存，后台按中文重新转写了英文。");
      onSaved?.(saved);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  };

  const updated = timeLabel(composition.updated_at);

  return (
    <section className="pod-composition-result" aria-label="画面指令">
      <div className="pod-composition-result-title">
        <span>PANELS</span>
        <h3>{updated ? `画面指令（更新于 ${updated}）` : "画面指令"}</h3>
      </div>
      <ul className="pod-composition-panels">
        {COMPOSITION_PANEL_SLOTS.map((slot) => (
          <li key={slot.key}>
            <label>
              <b>{slot.label}</b>
              <AutoGrowTextarea
                value={drafts[slot.key]}
                maxLength={COMPOSITION_PANEL_MAX_LENGTH}
                disabled={busy}
                aria-label={`${slot.label}的画面指令`}
                placeholder="填写这一格要怎么拍"
                onChange={(event) => {
                  const value = event.currentTarget.value;
                  setDrafts((current) => ({ ...current, [slot.key]: value }));
                  setError("");
                  setNotice("");
                }}
              />
            </label>
          </li>
        ))}
      </ul>
      <div className="pod-composition-actions">
        <button type="button" className="pod-composition-save" disabled={busy || !allFilled || !dirty} onClick={() => void save()}>
          {saving ? <><span className="iconfont icon-loading" aria-hidden="true" />保存中…</> : "保存"}
        </button>
      </div>
      {error && <p className="pod-composition-error" role="alert">{error}</p>}
      {!error && notice && <p className="pod-composition-notice" role="status">{notice}</p>}
    </section>
  );
}
