import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import type {
  PodBatchRetryCandidate,
  PodBatchRetryRequest,
  PodColumnRetryGroupCandidate,
} from "../data/podBatchRetry";

type Props = {
  open: boolean;
  imageCandidates: readonly PodBatchRetryCandidate[];
  titleCandidates: readonly PodBatchRetryCandidate[];
  columnBatch?: boolean;
  columnImageGroups?: readonly PodColumnRetryGroupCandidate[];
  busy: boolean;
  onClose: () => void;
  onSubmit: (request: PodBatchRetryRequest) => void;
};

type CandidateKind = "image" | "title";

function candidateKey(kind: CandidateKind, styleIndex: number): string {
  return `${kind}:${styleIndex}`;
}

function allCandidateKeys(imageCandidates: readonly PodBatchRetryCandidate[], titleCandidates: readonly PodBatchRetryCandidate[]): Set<string> {
  return new Set([
    ...imageCandidates.map((candidate) => candidateKey("image", candidate.styleIndex)),
    ...titleCandidates.map((candidate) => candidateKey("title", candidate.styleIndex)),
  ]);
}

export function PodFailedRetryDialog({
  open,
  imageCandidates,
  titleCandidates,
  columnBatch = false,
  columnImageGroups,
  busy,
  onClose,
  onSubmit,
}: Props) {
  const candidateSignature = useMemo(() => [
    ...imageCandidates.map((candidate) => `image:${candidate.styleIndex}`),
    ...titleCandidates.map((candidate) => `title:${candidate.styleIndex}`),
  ].join(","), [imageCandidates, titleCandidates]);
  const [selected, setSelected] = useState(() => allCandidateKeys(imageCandidates, titleCandidates));

  useEffect(() => {
    if (open) setSelected(allCandidateKeys(imageCandidates, titleCandidates));
  }, [open, candidateSignature]);

  if (!open) return null;

  const selectedImageStyleIndices = imageCandidates.filter((candidate) => selected.has(candidateKey("image", candidate.styleIndex))).map((candidate) => candidate.styleIndex);
  const selectedTitleStyleIndices = titleCandidates.filter((candidate) => selected.has(candidateKey("title", candidate.styleIndex))).map((candidate) => candidate.styleIndex);
  const noCandidates = !imageCandidates.length && !titleCandidates.length;
  const toggleCandidate = (kind: CandidateKind, styleIndex: number) => {
    const key = candidateKey(kind, styleIndex);
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  const toggleColumnGroup = (styleIndices: readonly number[]) => {
    setSelected((current) => {
      const next = new Set(current);
      const allChecked = styleIndices.every((index) => next.has(candidateKey("image", index)));
      for (const index of styleIndices) {
        const key = candidateKey("image", index);
        if (allChecked) next.delete(key);
        else next.add(key);
      }
      return next;
    });
  };
  const useColumnGroups = columnBatch && Boolean(columnImageGroups);

  // portal 到 body：tab 面板 fill-mode 动画的层叠上下文会锁住 fixed 层 z-index，被顶栏盖住
  return createPortal(<div className="pod-failed-retry-backdrop" role="presentation" onMouseDown={() => !busy && onClose()}>
    <section className="pod-failed-retry-dialog" role="dialog" aria-modal="true" aria-labelledby="pod-failed-retry-title" onMouseDown={(event) => event.stopPropagation()}>
      <header><div><span>FAILED ITEMS</span><h2 id="pod-failed-retry-title">批量重试失败项</h2><p>{useColumnGroups ? "默认已选中全部失败项；图片按 4 款一组重试并自动复用已完成轮次，标题失败仅重生标题。" : "默认已选中全部可重试款式；图片失败会整款重新生成，标题失败只会重生标题。"}</p></div><button type="button" onClick={onClose} disabled={busy} aria-label="关闭批量重试">×</button></header>
      {noCandidates
        ? <p className="pod-failed-retry-empty">当前批次没有可批量重试的失败款式。</p>
        : <div className="pod-failed-retry-groups">
          {useColumnGroups
            ? <ColumnGroupCandidates groups={columnImageGroups ?? []} selected={selected} disabled={busy} onToggle={toggleColumnGroup} />
            : <CandidateGroup title="图片失败（整款重生成）" kind="image" candidates={imageCandidates} selected={selected} disabled={busy} onToggle={toggleCandidate} />}
          <CandidateGroup title="标题失败（仅重生标题）" kind="title" candidates={titleCandidates} selected={selected} disabled={busy} onToggle={toggleCandidate} />
        </div>}
      <footer><button type="button" onClick={onClose} disabled={busy}>取消</button><button type="button" className="pod-failed-retry-select-all" disabled={busy || noCandidates} onClick={() => setSelected(allCandidateKeys(imageCandidates, titleCandidates))}>重试全部失败</button><button type="button" className="pod-failed-retry-confirm" disabled={busy || !selectedImageStyleIndices.length && !selectedTitleStyleIndices.length} onClick={() => onSubmit({ image_style_indices: selectedImageStyleIndices, title_style_indices: selectedTitleStyleIndices })}>{busy ? "正在提交" : <>确认重试（图片 {selectedImageStyleIndices.length} 款，标题 {selectedTitleStyleIndices.length} 款）</>}</button></footer>
    </section>
  </div>, document.body);
}

function ColumnGroupCandidates({ groups, selected, disabled, onToggle }: {
  groups: readonly PodColumnRetryGroupCandidate[];
  selected: ReadonlySet<string>;
  disabled: boolean;
  onToggle: (styleIndices: readonly number[]) => void;
}) {
  const totalStyles = groups.reduce((sum, group) => sum + group.styleIndices.length, 0);
  return <section className="pod-failed-retry-group">
    <h3>图片失败（按组重跑，复用已完成轮次）<small>{groups.length} 组 · {totalStyles} 款</small></h3>
    {!groups.length ? <p>暂无可重试图片组。</p> : <ul>{groups.map((group) => {
      const checked = group.styleIndices.every((index) => selected.has(candidateKey("image", index)));
      return <li key={`group:${group.groupIndex}`}><label><input type="checkbox" checked={checked} disabled={disabled} onChange={() => onToggle(group.styleIndices)} /><span><b>{group.title}</b><small>{group.reuseSummary} · {group.reason}</small></span></label></li>;
    })}</ul>}
  </section>;
}

function CandidateGroup({ title, kind, candidates, selected, disabled, onToggle }: {
  title: string;
  kind: CandidateKind;
  candidates: readonly PodBatchRetryCandidate[];
  selected: ReadonlySet<string>;
  disabled: boolean;
  onToggle: (kind: CandidateKind, styleIndex: number) => void;
}) {
  return <section className="pod-failed-retry-group">
    <h3>{title}<small>{candidates.length} 款</small></h3>
    {!candidates.length ? <p>暂无可重试款式。</p> : <ul>{candidates.map((candidate) => {
      const key = candidateKey(kind, candidate.styleIndex);
      return <li key={key}><label><input type="checkbox" checked={selected.has(key)} disabled={disabled} onChange={() => onToggle(kind, candidate.styleIndex)} /><span><b>{candidate.title}</b><small>款式 #{String(candidate.styleIndex).padStart(3, "0")} · {candidate.reason}</small></span></label></li>;
    })}</ul>}
  </section>;
}
