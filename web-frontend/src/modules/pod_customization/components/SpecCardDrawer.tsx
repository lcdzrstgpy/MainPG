import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { podCustomizationApi } from "../api/podCustomizationApi";
import {
  cloneSpecCardConfig,
  createEmptySpecCard,
  emptySpecCardCells,
  specCardSummaryText,
} from "../data/podCustomizationModel";
import type {
  PodBatchStatus,
  SpecCardConfig,
  SpecCardCorner,
  SpecCardReprintResponse,
  SpecCardStyle,
} from "../types";
import { SpecCardAppearanceControls } from "./SpecCardAppearanceControls";
import { SpecCardPreview } from "./SpecCardPreview";
import { SpecCardTableEditor } from "./SpecCardTableEditor";

export const SPEC_CARD_DRAWER_TITLE = "批量添加尺寸";
export const SPEC_CARD_FROZEN_NOTICE = "配置已冻结；改动将在下一批次生效";
export const SPEC_CARD_UNLOCKED_NOTICE = "已解锁：保存的改动只用于下一批次，运行中的批次不会被重印。";
export const SPEC_CARD_REEXPORT_NOTICE = "该批次需重新导出";

/** 允许全批重印的终态批次状态。 */
const SPEC_CARD_REPRINTABLE_STATUSES = new Set<PodBatchStatus>(["completed", "partial_failure", "failed"]);

export type SpecCardDrawerMode = "editable" | "frozen" | "terminal";

/**
 * 草稿（还没发起批次）可自由编辑；
 * 终态（completed / partial_failure / failed）可编辑并全批重印；
 * 生成中（含暂停）只读冻结。
 */
export function specCardDrawerMode(status?: PodBatchStatus | null): SpecCardDrawerMode {
  if (!status) return "editable";
  return SPEC_CARD_REPRINTABLE_STATUSES.has(status) ? "terminal" : "frozen";
}

/** 抽屉只需要这几个批次字段：重印目标、状态门槛与总数。 */
export type SpecCardBatchContext = {
  id: string;
  status: PodBatchStatus;
  count: number;
};

type Props = {
  open: boolean;
  config: SpecCardConfig;
  batch?: SpecCardBatchContext | null;
  baseTemplateId?: string;
  onClose: () => void;
  onSave: (config: SpecCardConfig) => void;
  onReprinted?: (batchId: string, result: SpecCardReprintResponse) => void;
};

type ReprintProgress = { done: number; total: number };

export function SpecCardDrawer({ open, config, batch, baseTemplateId, onClose, onSave, onReprinted }: Props) {
  const [cells, setCells] = useState<string[][]>(() => cloneSpecCardConfig(config).cells);
  const [style, setStyle] = useState<SpecCardStyle>(config.style);
  const [corner, setCorner] = useState<SpecCardCorner>(config.corner);
  const [unlocked, setUnlocked] = useState(false);
  const [reprinting, setReprinting] = useState(false);
  const [reprintProgress, setReprintProgress] = useState<ReprintProgress | null>(null);
  const [reprintResult, setReprintResult] = useState<SpecCardReprintResponse | null>(null);
  const [reprintError, setReprintError] = useState("");

  const mode = specCardDrawerMode(batch?.status);
  const readOnly = mode === "frozen" && !unlocked;

  useEffect(() => {
    if (!open) return;
    // 每次打开都以页面上的当前配置为起点；编辑期间不回灌，避免打字被重置。
    const next = cloneSpecCardConfig(config);
    setCells(next.cells);
    setStyle(next.style);
    setCorner(next.corner);
    setUnlocked(false);
    setReprinting(false);
    setReprintProgress(null);
    setReprintResult(null);
    setReprintError("");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose, open]);

  if (!open) return null;

  const currentConfig = (): SpecCardConfig => ({
    enabled: true,
    style,
    corner,
    cells: cells.map((row) => [...row]),
  });

  const saveConfig = () => {
    onSave(currentConfig());
    onClose();
  };

  const restoreDefault = () => {
    const next = createEmptySpecCard();
    setCells(next.cells);
    setStyle(next.style);
    setCorner(next.corner);
  };

  const clearCells = () => {
    setCells(emptySpecCardCells(cells.length, cells[0]?.length ?? 1));
  };

  const reprintBatch = async () => {
    if (!batch || reprinting) return;
    const next = currentConfig();
    setReprinting(true);
    setReprintError("");
    setReprintResult(null);
    // 后端按整批一次处理，只有完成时才返回逐款汇总，因此进度从 0/总数 直接跳到 总数/总数。
    setReprintProgress({ done: 0, total: batch.count });
    try {
      const result = await podCustomizationApi.reprintSpecCard(batch.id, {
        cells: next.cells,
        style: next.style,
        corner: next.corner,
      });
      setReprintProgress({ done: result.reprinted, total: batch.count });
      setReprintResult(result);
      onSave(next);
      onReprinted?.(batch.id, result);
      // 用户规格：重印完成后关闭抽屉回到 POD 页面（结果摘要由页面 toast 呈现）。
      onClose();
    } catch (cause) {
      setReprintProgress(null);
      setReprintError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setReprinting(false);
    }
  };

  // Portal 到 body：抽屉必须盖住工作区外壳里的「返回顶部」悬浮按钮（z-index 80）。
  // 若挂在页面内容里，父级层叠上下文会把抽屉压到按钮之下（2026-09-10 用户反馈）。
  return createPortal(
    <div className="pod-spec-card-drawer-layer">
      <button type="button" className="pod-spec-card-drawer-backdrop" onClick={onClose} aria-label="关闭" />
      <aside className="pod-spec-card-drawer" role="dialog" aria-modal="true" aria-label={SPEC_CARD_DRAWER_TITLE}>
        <header className="pod-spec-card-drawer-header">
          <div>
            <span>SPEC CARD · 素材图</span>
            <h2>{SPEC_CARD_DRAWER_TITLE}</h2>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">×</button>
        </header>

        {mode === "frozen" && <p className="pod-spec-card-frozen-banner" role="status">{SPEC_CARD_FROZEN_NOTICE}</p>}
        {mode === "terminal" && <p className="pod-spec-card-terminal-banner" role="status">当前批次已结束，可编辑后全批重印；重印完成后需要重新导出。</p>}

        <div className="pod-spec-card-drawer-body">
          <section className="pod-spec-card-section" aria-label="尺寸详情">
            <div className="pod-spec-card-section-title">
              <span>TABLE</span>
              <h3>尺寸详情</h3>
            </div>
            <SpecCardTableEditor cells={cells} onChange={setCells} disabled={readOnly} />
          </section>

          <SpecCardAppearanceControls
            style={style}
            corner={corner}
            onStyleChange={setStyle}
            onCornerChange={setCorner}
            disabled={readOnly}
          />

          <SpecCardPreview cells={cells} style={style} corner={corner} baseTemplateId={baseTemplateId} />
        </div>

        <footer className="pod-spec-card-drawer-footer">
          <div className="pod-spec-card-footer-status">
            <p className="pod-spec-card-summary">{isConfiguredSummary(cells, style, corner)}</p>
            {mode === "frozen" && unlocked && <p className="pod-spec-card-unlocked-notice">{SPEC_CARD_UNLOCKED_NOTICE}</p>}
            {reprinting && reprintProgress && (
              <p className="pod-spec-card-reprint-progress" role="status">
                重印中 {reprintProgress.done}/{reprintProgress.total}
                <i className="pod-spec-card-reprint-meter" aria-hidden="true" />
              </p>
            )}
            {reprintResult && (
              <div className="pod-spec-card-reprint-result" role="status">
                <p>成功 {reprintResult.reprinted} / 失败 {reprintResult.failed}</p>
                {reprintResult.errors.length > 0 && (
                  <ul className="pod-spec-card-reprint-errors">
                    {reprintResult.errors.map((error) => (
                      <li key={error.style_index}>款式 #{error.style_index}：{error.message}</li>
                    ))}
                  </ul>
                )}
                {reprintResult.needs_re_export && <p className="pod-spec-card-reprint-reexport">{SPEC_CARD_REEXPORT_NOTICE}</p>}
              </div>
            )}
            {reprintError && <p className="pod-spec-card-reprint-error" role="alert">{reprintError}</p>}
          </div>

          <div className="pod-spec-card-footer-actions">
            {mode === "terminal" && (
              <button
                type="button"
                className="pod-primary-button pod-spec-card-reprint-button"
                disabled={reprinting}
                onClick={() => void reprintBatch()}
              >{reprinting ? "重印中…" : "保存并全批重印"}</button>
            )}
            {mode === "frozen" && !unlocked && (
              <button type="button" className="pod-spec-card-unlock-button" onClick={() => setUnlocked(true)}>
                解锁编辑（仅用于下一批次）
              </button>
            )}
            <button type="button" className="pod-spec-card-save-button" disabled={readOnly} onClick={saveConfig}>保存到本批次</button>
            <button type="button" disabled={readOnly} onClick={restoreDefault}>恢复默认</button>
            <button type="button" disabled={readOnly} onClick={clearCells}>清空</button>
          </div>
        </footer>
      </aside>
    </div>,
    document.body,
  );
}

function isConfiguredSummary(cells: string[][], style: SpecCardStyle, corner: SpecCardCorner): string {
  return specCardSummaryText({ enabled: true, style, corner, cells });
}
