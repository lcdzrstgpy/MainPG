import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { podCustomizationApi } from "../api/podCustomizationApi";
import { AutoGrowTextarea } from "./AutoGrowTextarea";
import { CompositionPanelsEditor } from "./CompositionPanelsEditor";
import type { PodComposition } from "../types";

type Props = {
  open: boolean;
  onClose: () => void;
  /** 生成/保存后回调，供页面同步提示与状态。 */
  onChanged?: (composition: PodComposition | null) => void;
};

const COMPOSITION_INPUT_MAX_LENGTH = 500;
const COMPOSITION_STAGES = ["正在理解构图需求…", "正在组织画面指令…"];
const COMPOSITION_STAGE_INTERVAL_MS = 1_800;

/**
 * 「构图定制」：写一句大白话 → 生成一份新模板（自动生效）。
 * 生成结果可继续手动微调；只负责「生成 + 微调」，模板的选择/重命名/删除在「构图模板管理」里。
 */
export function CompositionGenerateDrawer({ open, onClose, onChanged }: Props) {
  const [brief, setBrief] = useState("");
  const [composition, setComposition] = useState<PodComposition | null>(null);
  const [loading, setLoading] = useState(false);
  const [stageIndex, setStageIndex] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // 打开时载入当前生效模板，方便在生成前/后对照与微调。
  useEffect(() => {
    if (!open) return;
    let stopped = false;
    setError("");
    setNotice("");
    void (async () => {
      try {
        const latest = await podCustomizationApi.getLatestComposition();
        if (!stopped) setComposition(latest);
      } catch (cause) {
        if (!stopped) setError(cause instanceof Error ? cause.message : String(cause));
      }
    })();
    return () => {
      stopped = true;
    };
  }, [open]);

  useEffect(() => {
    if (!loading) {
      setStageIndex(0);
      return;
    }
    const timer = window.setInterval(() => {
      setStageIndex((current) => Math.min(current + 1, COMPOSITION_STAGES.length - 1));
    }, COMPOSITION_STAGE_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [loading]);

  const generate = async () => {
    const input = brief.trim();
    if (loading || !input) return;
    setError("");
    setNotice("");
    setLoading(true);
    try {
      const saved = await podCustomizationApi.generateComposition({ brief: input, locale: "zh-CN" });
      setComposition(saved);
      onChanged?.(saved);
      setNotice("已生成一份新模板并设为生效；可在下方手动微调每一格。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause) || "构图生成失败，请稍后重试。");
    } finally {
      setLoading(false);
    }
  };

  if (!open) return null;

  return createPortal(
    <div className="pod-composition-drawer-layer">
      <button type="button" className="pod-composition-drawer-backdrop" onClick={onClose} aria-label="关闭" />
      <aside className="pod-composition-drawer" role="dialog" aria-modal="true" aria-label="构图定制">
        <header className="pod-composition-drawer-header">
          <div>
            <span>COMPOSITION · 视角与构图</span>
            <h2>构图定制</h2>
            <p>写一句大白话生成一份新模板并自动生效；生成后可在下方手动微调每一格。</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">×</button>
        </header>

        <div className="pod-composition-drawer-body">
          <section className="pod-composition-editor" aria-label="构图描述">
            <AutoGrowTextarea
              value={brief}
              maxLength={COMPOSITION_INPUT_MAX_LENGTH}
              disabled={loading}
              aria-label="用大白话描述四张图的视角与构图"
              placeholder={
                "描述四张图分别怎么拍：每张都要写到且互不相同；只写机位与角度、景别、构图留白、光线、背景处理，不要写具体物品或道具。\n" +
                "例：主图略低机位斜侧 45 度中全景、主体居三分点留白；细节图 A 微距俯拍；细节图 B 四分之三近景；素材图正面平视居中、纯净背景。"
              }
              onChange={(event) => {
                setBrief(event.currentTarget.value);
                setError("");
                setNotice("");
              }}
            />
            <div className="pod-composition-editor-meta">
              <span className={loading ? "pod-composition-stage" : ""}>
                {loading ? COMPOSITION_STAGES[stageIndex] : "固定角色：主图 / 细节图 A / 细节图 B / 素材图"}
              </span>
              <small>{brief.length}/{COMPOSITION_INPUT_MAX_LENGTH}</small>
            </div>
            <div className="pod-composition-actions">
              <button type="button" className="pod-composition-generate" disabled={loading || !brief.trim()} onClick={() => void generate()}>
                {loading ? <><span className="iconfont icon-loading" aria-hidden="true" />{COMPOSITION_STAGES[stageIndex]}</> : <><span className="iconfont icon-robot" aria-hidden="true" />生成并新增模板</>}
              </button>
            </div>
            {error && <p className="pod-composition-error" role="alert">{error}</p>}
            {!error && notice && <p className="pod-composition-notice" role="status">{notice}</p>}
          </section>

          {composition ? (
            <CompositionPanelsEditor
              composition={composition}
              disabled={loading}
              onSaved={(saved) => {
                setComposition(saved);
                onChanged?.(saved);
              }}
            />
          ) : (
            <section className="pod-composition-result" aria-label="画面指令">
              <div className="pod-composition-result-title">
                <span>PANELS</span>
                <h3>画面指令</h3>
              </div>
              <p className="pod-composition-empty">
                还没有模板；写一句大白话点「生成并新增模板」，或先在「构图模板管理」里选一份。
              </p>
            </section>
          )}
        </div>
      </aside>
    </div>,
    document.body,
  );
}
