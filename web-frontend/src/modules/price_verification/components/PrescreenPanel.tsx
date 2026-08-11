import { useState } from "react";
import type { PrescreenSettings } from "../types";
import { SectionHelp } from "./SectionHelp";
import { WorkflowActionBar } from "./WorkflowActionBar";

type Props = {
  isChecking: boolean;
  totalItems: number;
  totalSkc: number;
  passedItems: number;
  prescreen: PrescreenSettings | null;
  onPrescreenChange: (minAdjustedPriceCny: string) => Promise<boolean>;
  onRefresh: () => void;
  onContinue: () => void;
};

export function PrescreenPanel({ isChecking, totalItems, totalSkc, passedItems, prescreen, onPrescreenChange, onRefresh, onContinue }: Props) {
  const [threshold, setThreshold] = useState(prescreen?.min_adjusted_price_cny != null && prescreen.min_adjusted_price_cny !== "" ? String(prescreen.min_adjusted_price_cny) : "");
  const [saving, setSaving] = useState(false);
  const thresholdActive = prescreen?.min_adjusted_price_cny != null && prescreen.min_adjusted_price_cny !== "";
  const filteredCount = Math.max(0, totalSkc - passedItems);

  const save = async () => {
    setSaving(true);
    try {
      return await onPrescreenChange(threshold.trim());
    } finally {
      setSaving(false);
    }
  };

  const saveAndContinue = async () => {
    if (await save()) onContinue();
  };

  return <section className="price-verification-panel price-verification-batch-panel">
    <div className="price-verification-panel-heading"><div><p className="eyebrow">STEP 01 · BATCH</p><h2>数据初筛<SectionHelp title="插件在 Temu“批量查看并确认申报价”页采集本页数据（每页最多 50 个 SKC，各 SKC 可含多条 SKU 报价）入库后，后台先按此处的初筛条件过滤：调整后申报价（CNY）低于门槛或缺失的商品不会进入 STEP 02 人工确认。新采集会覆盖旧数据，只保留最新一批。不填门槛则全部进入。" /></h2></div><div className="price-verification-heading-actions"><button className="price-verification-secondary-button" onClick={onRefresh} disabled={isChecking}>{isChecking ? "正在刷新…" : "↻ 刷新"}</button></div></div>
    <div className="price-verification-batch-bar">
      <div className="price-verification-current-batch">
        <span>本页采集</span>
        <strong>{totalSkc} 个 SKC</strong>
        <small>{totalItems} 条 SKU 报价（每个 SKC 可含多个 SKU 规格）· 新采集覆盖旧数据，只保留最新一批</small>
      </div>
      <label className="price-verification-batch-switch">
        <span>初筛条件</span>
        <input
          type="number"
          min={0}
          step="0.01"
          value={threshold}
          placeholder="不填 = 不限制"
          disabled={isChecking || saving}
          onChange={(event) => setThreshold(event.target.value)}
        />
        <small>调整后申报价（CNY）需 &gt; 该值</small>
      </label>
    </div>
    <div className="price-verification-batch-list">
      <div className="price-verification-batch-row is-current">
        <span className="price-verification-batch-mark">初筛</span>
        <strong>{thresholdActive ? `调整后申报价（CNY）> ${prescreen?.min_adjusted_price_cny}` : "未设置初筛条件"}</strong>
        <small>{totalSkc} 个 SKC / {totalItems} 条 SKU 报价 → 初筛后 {passedItems} 个 SKC 进入 STEP 02 人工确认{thresholdActive && filteredCount > 0 ? `（过滤掉 ${filteredCount} 个 SKC）` : ""}</small>
      </div>
    </div>
    <WorkflowActionBar label="数据初筛操作">
      <div className="price-verification-action-summary"><span>初筛后可审核</span><strong>{passedItems} 个 SKC</strong></div>
      <div className="price-verification-action-buttons">
        <button className="price-verification-secondary-button" onClick={() => void save()} disabled={isChecking || saving}>{saving ? "保存中…" : "仅保存"}</button>
        <button className="price-verification-primary-button" onClick={() => void saveAndContinue()} disabled={isChecking || saving || !totalSkc}>{saving ? "保存中…" : "保存并进入批次审核"}</button>
      </div>
    </WorkflowActionBar>
  </section>;
}
