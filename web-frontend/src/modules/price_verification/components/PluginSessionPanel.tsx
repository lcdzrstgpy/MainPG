import type { QuoteCaptureBatch } from "../types";
import { SectionHelp } from "./SectionHelp";

type Props = {
  isChecking: boolean;
  batches: QuoteCaptureBatch[];
  onRefresh: () => void;
  onCreateBatch: () => void;
  onActivateBatch: (id: string) => void;
};

function formatTime(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getMonth() + 1}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function PluginSessionPanel({ isChecking, batches, onRefresh, onCreateBatch, onActivateBatch }: Props) {
  const currentBatch = batches.find((batch) => batch.is_current);
  return <section className="price-verification-panel price-verification-batch-panel">
    <div className="price-verification-panel-heading"><div><p className="eyebrow">STEP 01 · BATCH</p><h2>核价批次管理<SectionHelp title="数据由你在 Temu“批量查看并确认申报价”页用插件自行采集，采集结果直接写入当前核价批次。本板块负责批次的新建、切换与状态查看，采集完成后回到此页刷新即可看到最新报价。" /></h2></div><div className="price-verification-heading-actions"><button className="price-verification-primary-button" onClick={onCreateBatch} disabled={isChecking}>＋ 新建批次</button><button className="price-verification-secondary-button" onClick={onRefresh} disabled={isChecking}>{isChecking ? "正在刷新…" : "↻ 刷新批次"}</button></div></div>
    <div className="price-verification-batch-bar"><div className="price-verification-current-batch"><span>当前核价批次</span><strong>{currentBatch ? currentBatch.name : "请先新建一个批次"}</strong><small>{currentBatch ? `已入库 ${currentBatch.quote_count} 条报价（${currentBatch.chunk_count} 页）` : "新建批次后，插件采集的数据才会写入"}</small></div>{batches.length > 1 ? <label className="price-verification-batch-switch"><span>切换批次</span><select value={currentBatch?.batch_id ?? ""} onChange={(event) => onActivateBatch(event.target.value)} disabled={isChecking}>{batches.map((batch) => <option key={batch.batch_id} value={batch.batch_id}>{batch.name}{batch.is_current ? "（当前）" : ""}</option>)}</select></label> : null}</div>
    {batches.length ? <div className="price-verification-batch-list">{batches.map((batch) => (
      <div className={`price-verification-batch-row${batch.is_current ? " is-current" : ""}`} key={batch.batch_id}>
        <span className="price-verification-batch-mark">{batch.is_current ? "当前" : ""}</span>
        <strong>{batch.name}</strong>
        <small>已入库 {batch.quote_count} 条报价 · {batch.chunk_count} 页{formatTime(batch.updated_at) ? ` · 更新于 ${formatTime(batch.updated_at)}` : ""}</small>
        {batch.is_current ? null : <button className="price-verification-secondary-button" onClick={() => onActivateBatch(batch.batch_id)} disabled={isChecking}>切换</button>}
      </div>
    ))}</div> : <div className="price-verification-empty-result"><span>◇</span><div><h3>暂无核价批次</h3><p>点击“新建批次”创建一个，然后在 Temu 页面用插件采集数据。</p></div></div>}
  </section>;
}
