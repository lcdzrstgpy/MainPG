import { useEffect, useState } from "react";
import type { ArchiveProductIdType, PrescreenSettings } from "../types";
import { SectionHelp } from "./SectionHelp";
import { WorkflowActionBar } from "./WorkflowActionBar";

type Props = {
  isChecking: boolean;
  totalItems: number;
  totalSkc: number;
  passedItems: number;
  prescreen: PrescreenSettings | null;
  storeName: string;
  archiveProductIdType: ArchiveProductIdType;
  onPrescreenChange: (minAdjustedPriceCny: string) => Promise<boolean>;
  onStoreNameChange: (storeName: string) => Promise<boolean>;
  onArchiveProductIdTypeChange: (archiveProductIdType: ArchiveProductIdType) => Promise<boolean>;
  onRefresh: () => void;
  onContinue: () => void;
};

export function PrescreenPanel({ isChecking, totalItems, totalSkc, passedItems, prescreen, storeName: savedStoreName, archiveProductIdType: savedArchiveProductIdType, onPrescreenChange, onStoreNameChange, onArchiveProductIdTypeChange, onRefresh, onContinue }: Props) {
  const [threshold, setThreshold] = useState(prescreen?.min_adjusted_price_cny != null && prescreen.min_adjusted_price_cny !== "" ? String(prescreen.min_adjusted_price_cny) : "");
  const [storeName, setStoreName] = useState(savedStoreName);
  const [archiveProductIdType, setArchiveProductIdType] = useState<ArchiveProductIdType>(savedArchiveProductIdType);
  const [saving, setSaving] = useState(false);
  const thresholdActive = prescreen?.min_adjusted_price_cny != null && prescreen.min_adjusted_price_cny !== "";
  const filteredCount = Math.max(0, totalSkc - passedItems);

  useEffect(() => setStoreName(savedStoreName), [savedStoreName]);
  useEffect(() => setArchiveProductIdType(savedArchiveProductIdType), [savedArchiveProductIdType]);

  const save = async () => {
    setSaving(true);
    try {
      const thresholdSaved = await onPrescreenChange(threshold.trim());
      if (!thresholdSaved) return false;
      const storeSaved = await onStoreNameChange(storeName.trim());
      if (!storeSaved) return false;
      return onArchiveProductIdTypeChange(archiveProductIdType);
    } finally {
      setSaving(false);
    }
  };

  const saveAndContinue = async () => {
    if (await save()) onContinue();
  };

  return <section className="price-verification-batch-panel">
    <div className="price-verification-panel-heading"><div><h2>数据初筛<SectionHelp title="插件在 Temu“批量查看并确认申报价”页采集本页数据（每页最多 50 个 SKC，各 SKC 可含多条 SKU 报价）入库后，后台先按此处的初筛条件过滤：调整后申报价（CNY）低于门槛或缺失的商品不会进入 STEP 02 人工确认。新采集会覆盖旧数据，只保留最新一批。不填门槛则全部进入。" /></h2></div></div>
    <div className="price-verification-batch-summary">
      <div className="price-verification-current-batch">
        <span>本页采集</span>
        <strong>{totalSkc} 个 SKC</strong>
        <small>{totalItems} 条 SKU 报价 · 每个 SKC 可含多个 SKU 规格</small>
      </div>
      <div className="price-verification-prescreen-fields">
        <label className="price-verification-batch-switch">
          <span>初筛条件</span>
          <input type="number" min={0} step="0.01" value={threshold} placeholder="不填 = 不限制" disabled={isChecking || saving} onChange={(event) => setThreshold(event.target.value)} />
          <small>调整后申报价（CNY）需 ≥ 该值</small>
        </label>
        <label className="price-verification-batch-switch">
          <span>店铺（可选）</span>
          <input type="text" maxLength={120} value={storeName} placeholder="例如：美区一店" disabled={isChecking || saving} onChange={(event) => setStoreName(event.target.value)} />
          <small>本批次入库产品归属店铺</small>
        </label>
        <label className="price-verification-batch-switch">
          <span>入库商品标识</span>
          <select value={archiveProductIdType} disabled={isChecking || saving} onChange={(event) => setArchiveProductIdType(event.target.value as ArchiveProductIdType)}>
            <option value="SKC">SKC（推荐）</option>
            <option value="SKU">SKU</option>
            <option value="SPU">SPU</option>
          </select>
          <small>STEP 03 货源关联完成后，按该标识写入产品库</small>
        </label>
      </div>
      <div className="price-verification-prescreen-result">
        <span>初筛结果</span>
        <strong>{passedItems} 个 SKC</strong>
        <small>{thresholdActive ? `申报价 ≥ ${prescreen?.min_adjusted_price_cny}；` : "未设置门槛；"}进入 STEP 02{filteredCount > 0 ? `，过滤 ${filteredCount} 个` : ""}</small>
        <WorkflowActionBar label="数据初筛操作">
          <div className="price-verification-action-buttons">
            <button className="price-verification-text-action" onClick={onRefresh} disabled={isChecking}>{isChecking ? "正在刷新…" : "↻ 刷新"}</button>
            <button className="price-verification-text-action" onClick={() => void save()} disabled={isChecking || saving}>{saving ? "保存中…" : "仅保存"}</button>
            <button className="price-verification-text-action" onClick={() => void saveAndContinue()} disabled={isChecking || saving || !totalSkc}>{saving ? "保存中…" : "保存并进入批次审核"}</button>
          </div>
        </WorkflowActionBar>
      </div>
    </div>
  </section>;
}
