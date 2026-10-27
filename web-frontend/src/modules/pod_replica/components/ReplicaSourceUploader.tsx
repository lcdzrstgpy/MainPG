import { useRef, type ClipboardEvent, type DragEvent } from "react";

import { PodAssetImage } from "../../pod_customization/data/usePodAssetUrl";
import { replicaAssetPath } from "../data/podReplicaModel";

/** 顶部「POD 样图」：单图预览、粘贴、拖拽、上传与替换的受控上传区。 */
export type ReplicaSourceView = {
  assetId: string;
  filename: string;
} | null;

type Props = {
  source: ReplicaSourceView;
  busy: boolean;
  onFiles: (files: File[]) => void;
  onClear: () => void;
};

function imageFiles(list: FileList | null | undefined): File[] {
  return Array.from(list ?? []).filter((file) => file.type.startsWith("image/"));
}

export function ReplicaSourceUploader({ source, busy, onFiles, onClear }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  // 只在明确的样图区域内消费剪贴板图片；文本框粘贴不受影响。
  const handlePaste = (event: ClipboardEvent<HTMLDivElement>) => {
    const files = imageFiles(event.clipboardData?.files);
    if (!files.length) return;
    event.preventDefault();
    onFiles(files);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    const files = imageFiles(event.dataTransfer?.files);
    if (!files.length) return;
    event.preventDefault();
    onFiles(files);
  };

  return (
    <section className="pod-replica-source" aria-label="POD 样图">
      <div className="pod-replica-section-title"><span>PATTERN SOURCE</span><h2>POD 样图</h2><small>只支持一张；图案将从这张图迁移到下方目标产品</small></div>
      <div
        className={`pod-replica-source-drop${source ? " has-image" : ""}`}
        role="group"
        aria-label="样图上传区"
        tabIndex={0}
        onPaste={handlePaste}
        onDrop={handleDrop}
        onDragOver={(event) => event.preventDefault()}
      >
        {source ? (
          <div className="pod-replica-source-preview">
            <PodAssetImage path={replicaAssetPath(source.assetId)} alt="POD 样图预览" />
            <div className="pod-replica-source-actions">
              <button type="button" disabled={busy} onClick={() => inputRef.current?.click()}>替换样图</button>
              <button type="button" disabled={busy} onClick={onClear}>移除</button>
            </div>
            <small title={source.filename}>{source.filename || "已上传样图"}</small>
          </div>
        ) : (
          <button type="button" className="pod-replica-source-empty" disabled={busy} onClick={() => inputRef.current?.click()}>
            <span className="iconfont icon-upload" aria-hidden="true" />
            <b>{busy ? "样图上传中…" : "粘贴、拖拽或点击上传 POD 样图"}</b>
            <small>一次只允许一张；多张一起粘贴会提示重新选择</small>
          </button>
        )}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
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
