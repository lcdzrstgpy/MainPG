import { createPortal } from "react-dom";

import { PodListingFieldsEditor, REPLICA_PRODUCT_NAME_MAX_LENGTH, type SkuField, type SkuFieldErrors } from "../../pod_customization/components/PodListingFieldsEditor";
import {
  REPLICA_ANALYSIS_STATUS_LABELS,
  REPLICA_EDITABLE_FACT_KEYS,
  REPLICA_LIST_FACT_KEYS,
  REPLICA_PRESENTATION_OPTIONS,
  REPLICA_TARGET_FACT_FIELDS,
  parseReplicaListFact,
  replicaAnalysisBlockReason,
  replicaCapabilityLabel,
  replicaEffectiveFactText,
  replicaFactConfidence,
  replicaFactNeedsConfirmation,
  type ReplicaTargetDraft,
} from "../data/podReplicaModel";
import type { ReplicaFactOverrides } from "../types";

type Props = {
  open: boolean;
  target: ReplicaTargetDraft | null;
  skuFieldErrors: SkuFieldErrors;
  skuLimitReached: boolean;
  showRequiredErrors: boolean;
  /** 该目标正在识别中（用于禁用触发按钮）。 */
  analyzing: boolean;
  onChangeProductName: (clientId: string, value: string) => void;
  onListingFieldChange: (clientId: string, key: "title_mode" | "suggested_price_usd" | "category_name", value: string) => void;
  onAddSku: (clientId: string) => void;
  onUpdateSku: (clientId: string, index: number, key: SkuField, value: string) => void;
  onRemoveSku: (clientId: string, index: number) => void;
  onOpenSpecCardDrawer: (clientId: string) => void;
  /** 触发 / 重新触发识图分析。 */
  onAnalyze: (clientId: string) => void;
  /** 确认识别结果（低置信度时由用户手动确认）。 */
  onConfirmAnalysis: (clientId: string) => void;
  /** 修正事实（写入覆盖项并标记已确认）。 */
  onFactOverride: (clientId: string, patch: ReplicaFactOverrides) => void;
  /** 切换展示偏好。 */
  onPresentationChange: (clientId: string, key: (typeof REPLICA_PRESENTATION_OPTIONS)[number]["key"], value: boolean) => void;
  onClose: () => void;
};

/** 编辑框文本 → 事实覆盖 patch：数字、数组、字符串三类分别解析。 */
function buildFactPatch(key: string, raw: string): ReplicaFactOverrides {
  if (key === "component_count") {
    const parsed = Number(raw.trim());
    return { component_count: raw.trim() === "" || !Number.isFinite(parsed) ? null : parsed };
  }
  if (REPLICA_LIST_FACT_KEYS.includes(key)) {
    return { [key]: parseReplicaListFact(raw) } as ReplicaFactOverrides;
  }
  return { [key]: raw.trim() === "" ? null : raw.trim() } as ReplicaFactOverrides;
}

function analysisHint(target: ReplicaTargetDraft): string {
  switch (target.analysisStatus) {
    case "pending": return "上传 POD 样图与该产品白底图后会自动识别，请稍候。";
    case "analyzing": return "正在识别产品事实…";
    case "needs_confirmation": return "有低置信度字段，请核对后点「确认识别结果」。";
    case "confirmed": return "识别结果已确认，可以直接提交。";
    case "failed": return target.analysisError || "识别失败，请重新识别。";
    default: return "";
  }
}

/**
 * 单个目标产品的抽屉：产品识别确认 + 商品信息。
 * 复刻不需要指示词：这里绝不出现图片风格、元素关键词或设计提示词输入框。
 */
export function ReplicaTargetDrawer({
  open,
  target,
  skuFieldErrors,
  skuLimitReached,
  showRequiredErrors,
  analyzing,
  onChangeProductName,
  onListingFieldChange,
  onAddSku,
  onUpdateSku,
  onRemoveSku,
  onOpenSpecCardDrawer,
  onAnalyze,
  onConfirmAnalysis,
  onFactOverride,
  onPresentationChange,
  onClose,
}: Props) {
  if (!open || !target) return null;
  const status = target.analysisStatus;
  const confirming = status === "needs_confirmation";
  const blocked = replicaAnalysisBlockReason(target);
  return createPortal(
    <div className="pod-replica-drawer-layer">
      <button type="button" className="pod-replica-drawer-backdrop" aria-label="关闭" onClick={onClose} />
      <aside className="pod-replica-drawer" role="dialog" aria-modal="true" aria-label="产品信息">
        <header className="pod-replica-drawer-header">
          <div><span>PRODUCT INFO</span><h2>产品信息</h2></div>
          <button type="button" onClick={onClose} aria-label="关闭">×</button>
        </header>

        <div className="pod-replica-drawer-body">
          <section className="pod-replica-analysis" aria-label="产品识别">
            <div className="pod-replica-analysis-head">
              <div><span>AI ANALYSIS</span><h3>产品识别</h3></div>
              <b className={`pod-replica-analysis-badge is-${status}`}>{REPLICA_ANALYSIS_STATUS_LABELS[status]}</b>
            </div>
            <p className={`pod-replica-analysis-hint${blocked && status !== "analyzing" ? " is-warning" : ""}`}>{analysisHint(target)}</p>

            {(target.sourceArtworkSpec || target.targetProductSpec) && (
              <dl className="pod-replica-facts">
                {REPLICA_TARGET_FACT_FIELDS.map(({ key, label }) => {
                  const override = (target.factOverrides as Record<string, unknown>)[key];
                  const hasOverride = override !== null && override !== undefined;
                  const editable = REPLICA_EDITABLE_FACT_KEYS.includes(key);
                  const confidence = replicaFactConfidence(target.targetProductSpec, key);
                  const needsConfirm = !hasOverride && replicaFactNeedsConfirmation(target.targetProductSpec, key);
                  const text = replicaEffectiveFactText(target, key);
                  return (
                    <div className="pod-replica-fact-row" key={key}>
                      <dt>
                        {label}
                        {hasOverride ? (
                          <em className="is-user">已修正</em>
                        ) : needsConfirm ? (
                          <em className="is-low">待确认 {Math.round(confidence * 100)}%</em>
                        ) : (
                          <em className="is-ok">{Math.round(confidence * 100)}%</em>
                        )}
                      </dt>
                      <dd>
                        {editable ? (
                          <input
                            aria-label={label}
                            value={text}
                            placeholder="（识别为空，可手动填写）"
                            onChange={(event) => onFactOverride(target.clientId, buildFactPatch(key, event.target.value))}
                          />
                        ) : (
                          <span>{text || "—"}</span>
                        )}
                      </dd>
                    </div>
                  );
                })}
              </dl>
            )}

            {target.candidateCapabilities.length > 0 && (
              <div className="pod-replica-capabilities">
                <span className="pod-replica-capabilities-label">候选能力（不代表本次必拍）</span>
                <div className="pod-replica-capability-tags">
                  {target.candidateCapabilities.map((id) => (
                    <span className="pod-replica-capability-tag" key={id}>{replicaCapabilityLabel(id)}</span>
                  ))}
                  {target.unknownCapabilities.map((id) => (
                    <span className="pod-replica-capability-tag is-unknown" key={`unknown-${id}`}>{id}（未识别）</span>
                  ))}
                </div>
              </div>
            )}

            <div className="pod-replica-preferences" aria-label="展示偏好">
              <span className="pod-replica-preferences-label">展示偏好（能力支持 ≠ 本次必拍）</span>
              {REPLICA_PRESENTATION_OPTIONS.map(({ key, label, hint }) => (
                <label className="pod-replica-preference" key={key} title={hint}>
                  <input
                    type="checkbox"
                    checked={target.presentationPreferences[key]}
                    onChange={(event) => onPresentationChange(target.clientId, key, event.target.checked)}
                  />
                  <span>{label}</span>
                  <small>{hint}</small>
                </label>
              ))}
            </div>

            <div className="pod-replica-analysis-actions">
              <button
                type="button"
                disabled={analyzing || !target.assetId}
                onClick={() => onAnalyze(target.clientId)}
              >
                {analyzing ? "识别中…" : target.targetProductSpec ? "重新识别" : "开始识别"}
              </button>
              {confirming && (
                <button type="button" className="is-primary" onClick={() => onConfirmAnalysis(target.clientId)}>确认识别结果</button>
              )}
            </div>
          </section>

          <label className="pod-replica-name-field">
            <span>产品名称<em>*</em></span>
            <input
              value={target.productName}
              maxLength={REPLICA_PRODUCT_NAME_MAX_LENGTH}
              aria-label="产品名称"
              aria-invalid={!target.productName.trim()}
              placeholder="例如：抱枕"
              onChange={(event) => onChangeProductName(target.clientId, event.target.value)}
            />
          </label>

          <PodListingFieldsEditor
            listingFields={target.listingFields}
            specCard={target.specCard}
            skuFieldErrors={skuFieldErrors}
            skuLimitReached={skuLimitReached}
            showRequiredErrors={showRequiredErrors}
            onListingFieldChange={(key, value) => onListingFieldChange(target.clientId, key, value)}
            onAddSku={() => onAddSku(target.clientId)}
            onUpdateSku={(index, key, value) => onUpdateSku(target.clientId, index, key, value)}
            onRemoveSku={(index) => onRemoveSku(target.clientId, index)}
            onOpenSpecCardDrawer={() => onOpenSpecCardDrawer(target.clientId)}
          />
        </div>
      </aside>
    </div>,
    document.body,
  );
}
