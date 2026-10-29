import { createPortal } from "react-dom";

import { PodListingFieldsEditor, REPLICA_PRODUCT_NAME_MAX_LENGTH, type SkuField, type SkuFieldErrors } from "../../pod_customization/components/PodListingFieldsEditor";
import {
  REPLICA_ANALYSIS_STATUS_LABELS,
  replicaAnalysisBlockReason,
  type ReplicaTargetDraft,
} from "../data/podReplicaModel";

type Props = {
  open: boolean;
  target: ReplicaTargetDraft | null;
  skuFieldErrors: SkuFieldErrors;
  skuLimitReached: boolean;
  showRequiredErrors: boolean;
  /** 该目标正在生成主图方案（用于禁用触发按钮）。 */
  analyzing: boolean;
  onChangeProductName: (clientId: string, value: string) => void;
  onListingFieldChange: (clientId: string, key: "title_mode" | "category_name", value: string) => void;
  onAddSku: (clientId: string) => void;
  onUpdateSku: (clientId: string, index: number, key: SkuField, value: string) => void;
  onRemoveSku: (clientId: string, index: number) => void;
  onOpenSpecCardDrawer: (clientId: string) => void;
  /** 生成 / 重新生成该款的主图方案。 */
  onAnalyze: (clientId: string) => void;
  onClose: () => void;
};

function analysisHint(target: ReplicaTargetDraft): string {
  switch (target.analysisStatus) {
    case "pending": return "上传样图与该产品白底图后会自动生成主图方案，请稍候。";
    case "analyzing": return "正在生成主图方案（图案怎么迁移、主图怎么拍）…";
    case "ready": return "主图方案已生成；提交后按此方案出主图，再由构图模板换视角派生三张。";
    case "failed": return target.analysisError || "主图方案生成失败，请重新生成。";
    default: return "";
  }
}

/**
 * 单个目标产品的抽屉：主图方案状态 + 商品信息。
 * 复刻不需要指示词，也不展示方案正文：这里绝不出现图片风格、元素关键词或设计提示词输入框。
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
  onClose,
}: Props) {
  if (!open || !target) return null;
  const status = target.analysisStatus;
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
          <section className="pod-replica-analysis" aria-label="主图方案">
            <div className="pod-replica-analysis-head">
              <div><span>MAIN IMAGE PLAN</span><h3>主图方案</h3></div>
              <b className={`pod-replica-analysis-badge is-${status}`}>{REPLICA_ANALYSIS_STATUS_LABELS[status]}</b>
            </div>
            <p className={`pod-replica-analysis-hint${blocked && status !== "analyzing" ? " is-warning" : ""}`}>{analysisHint(target)}</p>
            <div className="pod-replica-analysis-actions">
              <button
                type="button"
                disabled={analyzing || !target.assetId}
                onClick={() => onAnalyze(target.clientId)}
              >
                {analyzing ? "生成中…" : target.analysisId ? "重新生成方案" : "生成主图方案"}
              </button>
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

        <footer className="pod-replica-drawer-footer">
          <span>改动实时保存到本批次草稿，提交时一并生效。</span>
          <button type="button" className="is-primary" onClick={onClose}>保存并关闭</button>
        </footer>
      </aside>
    </div>,
    document.body,
  );
}
