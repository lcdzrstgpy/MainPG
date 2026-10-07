import { useRef, type ClipboardEvent, type DragEvent } from "react";

import { PodAssetImage } from "../../pod_customization/data/usePodAssetUrl";
import { isReplicaTargetComplete, replicaAssetPath, type ReplicaTargetDraft } from "../data/podReplicaModel";

type Props = {
  targets: readonly ReplicaTargetDraft[];
  busy: boolean;
  onFiles: (files: File[]) => void;
  onRemove: (clientId: string) => void;
  onOpen: (clientId: string) => void;
};

function imageFiles(list: FileList | null | undefined): File[] {
  return Array.from(list ?? []).filter((file) => file.type.startsWith("image/"));
}

/** 中部「目标产品」白底图卡片列表：按添加顺序显示名称、缩略图与信息完整度。 */
export function ReplicaTargetList({ targets, busy, onFiles, onRemove, onOpen }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDrop = (event: DragEvent<HTMLElement>) => {
    const files = imageFiles(event.dataTransfer?.files);
    if (!files.length) return;
    event.preventDefault();
    onFiles(files);
  };

  const handlePaste = (event: ClipboardEvent<HTMLElement>) => {
    const files = imageFiles(event.clipboardData?.files);
    if (!files.length) return;
    event.preventDefault();
    onFiles(files);
  };

  return (
    <section className="pod-replica-targets" aria-label="目标产品">
      <div className="pod-replica-section-title">
        <span>TARGET PRODUCTS</span>
        <h2>目标产品</h2>
        <small>每张白底图对应一款；支持多文件拖拽或逐张粘贴</small>
      </div>
      <div
        className={`pod-replica-target-grid${targets.length ? "" : " is-empty"}`}
        onDrop={handleDrop}
        onDragOver={(event) => event.preventDefault()}
        onPaste={handlePaste}
        tabIndex={0}
        aria-label="目标产品拖拽或粘贴区"
      >
        {targets.map((target, index) => {
          const complete = isReplicaTargetComplete(target);
          return (
            <article className={`pod-replica-target-card${complete ? " is-complete" : ""}`} key={target.clientId}>
              <span className="pod-replica-target-thumb">
                {target.assetId
                  ? <PodAssetImage path={replicaAssetPath(target.assetId)} alt="" loading="lazy" />
                  : <span className="pod-replica-target-empty">待上传</span>}
              </span>
              <span className="pod-replica-target-meta">
                <b>{target.productName.trim() || `产品 ${index + 1}`}</b>
                <small className={complete ? "is-ok" : "is-warning"}>{complete ? "信息完整" : "待完善"}</small>
              </span>
              <button type="button" className="pod-replica-target-edit" disabled={busy} aria-label={`编辑第 ${index + 1} 个产品的店小秘导出信息与尺寸图`} onClick={() => onOpen(target.clientId)}>导出信息编辑</button>
              <button type="button" className="pod-replica-target-remove" disabled={busy} aria-label={`删除第 ${index + 1} 个产品`} onClick={() => onRemove(target.clientId)}>×</button>
            </article>
          );
        })}
        <button type="button" className="pod-replica-target-add-card" disabled={busy} onClick={() => inputRef.current?.click()}><span className="iconfont icon-plus" aria-hidden="true" />添加白底图</button>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        multiple
        hidden
        onChange={(event) => {
          const files = imageFiles(event.target.files);
          event.target.value = "";
          if (files.length) onFiles(files);
        }}
      />
    </section>
  );
}
