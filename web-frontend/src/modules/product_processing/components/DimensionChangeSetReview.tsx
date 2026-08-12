import { useEffect, useState } from "react";

import {
  acceptDimensionChangeItem,
  acceptDimensionChangeSet,
  getDimensionChangeSet,
  rejectDimensionChangeItem,
} from "../api/dimensionCanvasApi";
import type { DimensionChangeSet, PhysicalDimensions } from "../types/dimensionCanvas";

type Props = {
  changeSetId: string;
  onChanged?: () => void;
};

function dimensionsText(dimensions: PhysicalDimensions): string {
  return (["length", "width", "height"] as const)
    .map((key) => dimensions[key].valueCm == null ? "—" : `${dimensions[key].valueCm} cm`)
    .join(" × ");
}

export function DimensionChangeSetReview({ changeSetId, onChanged }: Props) {
  const [changeSet, setChangeSet] = useState<DimensionChangeSet | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setChangeSet(await getDimensionChangeSet(changeSetId));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [changeSetId]);

  const apply = async (action: "all" | "accept" | "reject", itemId = "") => {
    setBusyId(itemId || "all");
    setError("");
    try {
      const next = action === "all"
        ? await acceptDimensionChangeSet(changeSetId)
        : action === "accept"
          ? await acceptDimensionChangeItem(changeSetId, itemId)
          : await rejectDimensionChangeItem(changeSetId, itemId);
      setChangeSet(next);
      onChanged?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyId("");
    }
  };

  return (
    <section className="dimension-review-panel">
      <header className="dimension-review-head">
        <div><span>尺寸画布返回</span><h2>{changeSet?.itemCount ?? 0} 项待审核变更</h2></div>
        <button className="primary" onClick={() => apply("all")} disabled={!changeSet || busyId !== "" || changeSet.items.every((item) => item.status === "conflict")}>接受全部无冲突项</button>
      </header>
      {error && <div className="dimension-banner is-error">{error}</div>}
      {loading && !changeSet ? <p>正在加载尺寸图差异…</p> : changeSet?.items.map((item) => (
        <article key={item.id} className={`dimension-review-item status-${item.status}`}>
          <header>
            <div><strong>{item.skc || item.dimensionItemId}</strong><span>{item.targetSlotId}</span></div>
            <span className="dimension-review-status">{item.status}</span>
          </header>
          <div className="dimension-review-diff">
            <figure><figcaption>当前目标图</figcaption>{item.oldImageUrl ? <img src={item.oldImageUrl} alt="当前目标图" /> : <span>空槽位</span>}</figure>
            <span aria-hidden="true">→</span>
            <figure><figcaption>新尺寸图</figcaption>{item.newImageUrl ? <img src={item.newImageUrl} alt="新尺寸图" /> : <span>渲染图不可用</span>}</figure>
            <dl><dt>商品本体尺寸</dt><dd>{dimensionsText(item.physicalDimensions)}</dd><dt>回写位置</dt><dd>{item.targetSlotId}</dd></dl>
          </div>
          {item.conflictReason && <div className="dimension-banner is-warning">冲突：{item.conflictReason}。请回到画布或预检处理，不会静默覆盖。</div>}
          <footer>
            <button onClick={() => apply("accept", item.id)} disabled={busyId !== "" || item.status === "conflict" || item.status === "accepted"}>接受此项</button>
            <button onClick={() => apply("reject", item.id)} disabled={busyId !== "" || item.status === "rejected"}>拒绝此项</button>
            {item.status === "conflict" && <button onClick={() => document.getElementById(`precheck-${item.dimensionItemId}`)?.scrollIntoView({ behavior: "smooth" })}>打开冲突处理</button>}
          </footer>
        </article>
      ))}
    </section>
  );
}
