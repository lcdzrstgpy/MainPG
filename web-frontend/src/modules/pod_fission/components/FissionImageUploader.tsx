import { useRef, type ClipboardEvent, type DragEvent } from "react";
import { PodAssetImage } from "../../pod_customization/data/usePodAssetUrl";

export type FissionUploaderProps = {
  onFile: (file: File) => void;
  onClear?: () => void;
  filename?: string;
  preview?: string;
  disabled?: boolean;
  uploading?: boolean;
};

type Props = FissionUploaderProps & { label: string; title: string; description: string; previewAlt: string };

/** 与爆款复刻保持一致：区域内支持粘贴、拖拽、选择及替换单张素材。 */
export function FissionImageUploader({ onFile, onClear, filename, preview, disabled, uploading, label, title, description, previewAlt }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const receive = (files: FileList | null | undefined) => {
    if (disabled) return;
    const file = Array.from(files ?? []).find((item) => item.type.startsWith("image/"));
    if (file) onFile(file);
  };
  const paste = (event: ClipboardEvent<HTMLDivElement>) => {
    if (disabled || !Array.from(event.clipboardData.files).some((file) => file.type.startsWith("image/"))) return;
    event.preventDefault();
    receive(event.clipboardData.files);
  };
  const drop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    receive(event.dataTransfer.files);
  };

  return <section className="pod-fission-upload" aria-label={title}>
    <div className="pod-fission-upload-heading"><b>{title}</b><small>{description}</small></div>
    <div className={`pod-fission-upload-zone${preview ? " has-image" : ""}`} role="group" aria-label={`${title}上传区`} aria-busy={uploading} tabIndex={disabled ? -1 : 0} onPaste={paste} onDrop={drop} onDragOver={(event) => event.preventDefault()}>
      {preview ? <div className="pod-fission-upload-preview">
        <PodAssetImage path={preview} alt={previewAlt} />
        <div className="pod-fission-upload-actions"><button type="button" disabled={disabled} onClick={() => inputRef.current?.click()}>{uploading ? "上传中…" : "替换图片"}</button>{onClear && <button type="button" disabled={disabled} onClick={onClear}>移除</button>}</div>
        <small title={filename}>{filename || "已上传图片"}</small>
      </div> : <button type="button" className="pod-fission-upload-empty" disabled={disabled} onClick={() => inputRef.current?.click()}>
        <span className={`iconfont ${uploading ? "icon-loading" : "icon-upload"}`} aria-hidden="true" />
        <b>{uploading ? "正在上传…" : "粘贴、拖拽或点击上传"}</b><small>单张图片 · 支持 JPG、PNG、WebP</small>
      </button>}
    </div>
    <input ref={inputRef} aria-label={label} type="file" accept="image/*" hidden disabled={disabled} onChange={(event) => { receive(event.target.files); event.target.value = ""; }} />
  </section>;
}
