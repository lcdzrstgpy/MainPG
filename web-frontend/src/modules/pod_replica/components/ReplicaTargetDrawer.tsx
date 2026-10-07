import { createPortal } from "react-dom";

import { PodListingFieldsEditor, type SkuField, type SkuFieldErrors } from "../../pod_customization/components/PodListingFieldsEditor";
import type { ReplicaTargetDraft } from "../data/podReplicaModel";

type Props = {
  open: boolean;
  target: ReplicaTargetDraft | null;
  skuFieldErrors: SkuFieldErrors;
  skuLimitReached: boolean;
  onChangeProductName: (clientId: string, value: string) => void;
  onListingFieldChange: (clientId: string, key: "title_mode" | "suggested_price_usd" | "category_name", value: string) => void;
  onAddSku: (clientId: string) => void;
  onUpdateSku: (clientId: string, index: number, key: SkuField, value: string) => void;
  onRemoveSku: (clientId: string, index: number) => void;
  onOpenSpecCardDrawer: (clientId: string) => void;
  onClose: () => void;
};

/**
 * 单个目标产品的商品信息抽屉：产品名称、商品类目与共享的店小秘上架编辑器。
 * 复刻不需要指示词：这里绝不出现图片风格、元素关键词或设计提示词输入框。
 */
export function ReplicaTargetDrawer({
  open,
  target,
  skuFieldErrors,
  skuLimitReached,
  onChangeProductName,
  onListingFieldChange,
  onAddSku,
  onUpdateSku,
  onRemoveSku,
  onOpenSpecCardDrawer,
  onClose,
}: Props) {
  if (!open || !target) return null;
  return createPortal(
    <div className="pod-replica-drawer-layer">
      <button type="button" className="pod-replica-drawer-backdrop" aria-label="关闭" onClick={onClose} />
      <aside className="pod-replica-drawer" role="dialog" aria-modal="true" aria-label="产品信息">
        <header className="pod-replica-drawer-header">
          <div><span>PRODUCT INFO</span><h2>产品信息</h2></div>
          <button type="button" onClick={onClose} aria-label="关闭">×</button>
        </header>

        <div className="pod-replica-drawer-body">
          <label className="pod-replica-name-field">
            <span>产品名称<em>*</em></span>
            <input
              value={target.productName}
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
