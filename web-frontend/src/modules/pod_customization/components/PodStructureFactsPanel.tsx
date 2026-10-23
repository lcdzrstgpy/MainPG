import { useEffect, useState } from "react";

import type { PodStructureFacts } from "../types";

/** 待人工确认的结构字段：只暴露对生图有硬约束意义的几项。 */
const STRUCTURE_FIELDS: { key: keyof PodStructureFacts; label: string; placeholder: string }[] = [
  { key: "tiers", label: "层数 / 层级", placeholder: "例如 4" },
  { key: "units_per_tier", label: "每层单元数", placeholder: "例如 14" },
  { key: "part_count", label: "部件总数", placeholder: "例如 4" },
  { key: "height_mm", label: "整体高度 (mm)", placeholder: "例如 31" },
  { key: "thickness_mm", label: "板厚 (mm)", placeholder: "例如 10.2" },
  { key: "material", label: "材质", placeholder: "例如 clear acrylic" },
  { key: "transparency", label: "透明度", placeholder: "none / low / medium / high" },
];

function factsToDraft(facts?: PodStructureFacts): Record<string, string> {
  const draft: Record<string, string> = {};
  STRUCTURE_FIELDS.forEach(({ key }) => {
    const value = facts?.[key];
    draft[key] = value === undefined || value === null ? "" : String(value);
  });
  return draft;
}

function draftToFacts(draft: Record<string, string>, base?: PodStructureFacts): PodStructureFacts {
  const facts: PodStructureFacts = {};
  STRUCTURE_FIELDS.forEach(({ key }) => {
    const raw = (draft[key] ?? "").trim();
    if (!raw) return;
    if (key === "material" || key === "transparency") {
      (facts as Record<string, unknown>)[key] = raw;
      return;
    }
    const parsed = Number(raw);
    if (Number.isFinite(parsed) && parsed >= 0) (facts as Record<string, unknown>)[key] = parsed;
  });
  if (base?.printable_regions?.length) facts.printable_regions = base.printable_regions;
  return facts;
}

type Props = {
  templateName: string;
  facts?: PodStructureFacts;
  busy?: boolean;
  saveLabel?: string;
  skipLabel?: string;
  onSave: (facts: PodStructureFacts) => void | Promise<void>;
  onSkip: () => void | Promise<void>;
};

/**
 * 结构事实表确认面板：低置信度时才出现，用户可改写数值后保存。
 * 保存结果会作为该模板后续批次的生图硬约束。
 */
export function PodStructureFactsPanel({
  templateName,
  facts,
  busy = false,
  saveLabel = "保存并应用",
  skipLabel = "跳过，直接使用",
  onSave,
  onSkip,
}: Props) {
  const [draft, setDraft] = useState<Record<string, string>>(() => factsToDraft(facts));

  useEffect(() => {
    setDraft(factsToDraft(facts));
  }, [facts]);

  return (
    <div className="pod-structure-confirm" role="group" aria-label="确认结构事实表">
      <header>
        <b>确认识别到的结构</b>
        <small>{templateName}</small>
      </header>
      <p>这些数值会作为生图硬约束（层数 / 部件数 / 尺寸 / 材质）。低置信度项请按实物修正，留空表示不启用该约束。</p>
      <div className="pod-structure-fields">
        {STRUCTURE_FIELDS.map(({ key, label, placeholder }) => (
          <label key={key}>{label}
            <input
              value={draft[key] ?? ""}
              placeholder={placeholder}
              disabled={busy}
              onChange={(event) => setDraft((current) => ({ ...current, [key]: event.target.value }))}
            />
          </label>
        ))}
      </div>
      <div className="pod-structure-actions">
        <button type="button" onClick={() => void onSkip()} disabled={busy}>{skipLabel}</button>
        <button type="button" className="pod-primary-button" onClick={() => void onSave(draftToFacts(draft, facts))} disabled={busy}>
          {busy ? "保存中…" : saveLabel}
        </button>
      </div>
    </div>
  );
}
