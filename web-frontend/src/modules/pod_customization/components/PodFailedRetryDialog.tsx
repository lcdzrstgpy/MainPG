import { useEffect, useMemo, useState } from "react";

import type { PodBatchRetryCandidate, PodBatchRetryRequest } from "../data/podBatchRetry";

type Props = {
  open: boolean;
  imageCandidates: readonly PodBatchRetryCandidate[];
  titleCandidates: readonly PodBatchRetryCandidate[];
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

export function PodFailedRetryDialog({ open, imageCandidates, titleCandidates, busy, onClose, onSubmit }: Props) {
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

  return <div className="pod-failed-retry-backdrop" role="presentation" onMouseDown={() => !busy && onClose()}>
    <section className="pod-failed-retry-dialog" role="dialog" aria-modal="true" aria-labelledby="pod-failed-retry-title" onMouseDown={(event) => event.stopPropagation()}>
      <header><div><span>FAILED ITEMS</span><h2 id="pod-failed-retry-title">批量重试失败项</h2><p>默认已选中全部可重试款式；图片失败会整款重新生成，标题失败只会重生标题。</p></div><button type="button" onClick={onClose} disabled={busy} aria-label="关闭批量重试">×</button></header>
      {noCandidates
        ? <p className="pod-failed-retry-empty">当前批次没有可批量重试的失败款式。</p>
        : <div className="pod-failed-retry-groups">
          <CandidateGroup title="图片失败（整款重生成）" kind="image" candidates={imageCandidates} selected={selected} disabled={busy} onToggle={toggleCandidate} />
          <CandidateGroup title="标题失败（仅重生标题）" kind="title" candidates={titleCandidates} selected={selected} disabled={busy} onToggle={toggleCandidate} />
        </div>}
      <footer><button type="button" onClick={onClose} disabled={busy}>取消</button><button type="button" className="pod-failed-retry-select-all" disabled={busy || noCandidates} onClick={() => setSelected(allCandidateKeys(imageCandidates, titleCandidates))}>重试全部失败</button><button type="button" className="pod-failed-retry-confirm" disabled={busy || !selectedImageStyleIndices.length && !selectedTitleStyleIndices.length} onClick={() => onSubmit({ image_style_indices: selectedImageStyleIndices, title_style_indices: selectedTitleStyleIndices })}>{busy ? "正在提交" : <>确认重试（图片 {selectedImageStyleIndices.length} 款，标题 {selectedTitleStyleIndices.length} 款）</>}</button></footer>
    </section>
  </div>;
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
