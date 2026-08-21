import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { defaultTemplateCalibration } from "../data/podCustomizationModel";
import { PodAssetImage } from "../data/usePodAssetUrl";
import type { PodTemplate, PodTemplateCalibration } from "../types";
import { TemplateCalibrationCanvas } from "./TemplateCalibrationCanvas";

type Props = {
  open: boolean;
  templates: PodTemplate[];
  selectedTemplateId?: string;
  busyAction: string;
  onClose: () => void;
  onSelect: (templateId: string) => void;
  onUpload: (file: File, name: string) => Promise<PodTemplate>;
  onCalibrate: (templateId: string) => Promise<PodTemplate>;
  onSaveCalibration: (templateId: string, calibration: PodTemplateCalibration) => Promise<PodTemplate>;
};

function calibrationLabel(template: PodTemplate): string {
  return ({
    pending: "待标定",
    calibrating: "AI 标定中",
    ready: "标定完成",
    failed: "标定失败",
  } as const)[template.calibration_status];
}

export function TemplateLibraryDrawer({
  open,
  templates,
  selectedTemplateId,
  busyAction,
  onClose,
  onSelect,
  onUpload,
  onCalibrate,
  onSaveCalibration,
}: Props) {
  const [scope, setScope] = useState<"system" | "personal">("system");
  const [activeTemplateId, setActiveTemplateId] = useState("");
  const [calibration, setCalibration] = useState<PodTemplateCalibration>(defaultTemplateCalibration());
  const [uploadName, setUploadName] = useState("");
  const [uploadFile, setUploadFile] = useState<File>();
  const fileRef = useRef<HTMLInputElement>(null);

  const visibleTemplates = useMemo(
    () => templates.filter((template) => template.source === scope),
    [scope, templates],
  );
  const activeTemplate = templates.find((template) => template.id === activeTemplateId)
    ?? templates.find((template) => template.id === selectedTemplateId)
    ?? visibleTemplates[0];

  useEffect(() => {
    if (!open) return;
    const selected = templates.find((template) => template.id === selectedTemplateId);
    if (selected) {
      setScope(selected.source);
      setActiveTemplateId(selected.id);
    } else if (visibleTemplates[0]) {
      setActiveTemplateId(visibleTemplates[0].id);
    }
  }, [open, selectedTemplateId, templates]);

  useEffect(() => {
    setCalibration(activeTemplate?.calibration ?? defaultTemplateCalibration());
  }, [activeTemplate?.id, activeTemplate?.updated_at]);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose, open]);

  const switchScope = (nextScope: "system" | "personal") => {
    setScope(nextScope);
    const first = templates.find((template) => template.source === nextScope);
    setActiveTemplateId(first?.id ?? "");
  };

  const submitUpload = async (event: FormEvent) => {
    event.preventDefault();
    if (!uploadFile || busyAction) return;
    try {
      const created = await onUpload(uploadFile, uploadName);
      setScope("personal");
      setActiveTemplateId(created.id);
      onSelect(created.id);
      setUploadFile(undefined);
      setUploadName("");
      if (fileRef.current) fileRef.current.value = "";
      await onCalibrate(created.id);
    } catch {
      // The page-level handlers surface the actionable API error.
    }
  };

  if (!open) return null;

  return (
    <div className="pod-template-drawer-layer">
      <button type="button" className="pod-template-drawer-backdrop" onClick={onClose} aria-label="关闭模板库" />
      <aside className="pod-template-drawer" role="dialog" aria-modal="true" aria-label="POD 产品模板库">
        <header className="pod-template-drawer-header">
          <div>
            <span>POD TEMPLATE LIBRARY</span>
            <h2>产品模板库</h2>
            <p>每个批次只绑定一个完成标定的产品模板。</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">×</button>
        </header>

        <div className="pod-template-scope-tabs" role="tablist" aria-label="模板来源">
          <button type="button" role="tab" aria-selected={scope === "system"} className={scope === "system" ? "is-active" : ""} onClick={() => switchScope("system")}>系统模板</button>
          <button type="button" role="tab" aria-selected={scope === "personal"} className={scope === "personal" ? "is-active" : ""} onClick={() => switchScope("personal")}>个人模板</button>
        </div>

        <div className="pod-template-drawer-body">
          <section className="pod-template-list-panel" aria-label={`${scope === "system" ? "系统" : "个人"}模板`}>
            {scope === "personal" && (
              <form className="pod-template-upload" onSubmit={(event) => void submitUpload(event)}>
                <label>模板名称<input value={uploadName} onChange={(event) => setUploadName(event.target.value)} placeholder="例如：帆布袋正面" /></label>
                <label className="pod-template-file">
                  <span className="iconfont icon-upload" aria-hidden="true" />
                  <span>{uploadFile?.name || "选择 PNG / JPG 产品模板"}</span>
                  <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => setUploadFile(event.target.files?.[0])} />
                </label>
                <button type="submit" className="pod-primary-button" disabled={!uploadFile || Boolean(busyAction)}>{busyAction === "upload" ? "上传中…" : "上传并 AI 标定"}</button>
              </form>
            )}

            <div className="pod-template-cards">
              {visibleTemplates.map((template) => (
                <button
                  key={template.id}
                  type="button"
                  className={`pod-template-card ${activeTemplate?.id === template.id ? "is-active" : ""} ${selectedTemplateId === template.id ? "is-selected" : ""}`}
                  onClick={() => {
                    setActiveTemplateId(template.id);
                    onSelect(template.id);
                  }}
                >
                  <PodAssetImage path={template.preview_url || template.original_url} alt="" loading="lazy" />
                  <span><b>{template.name}</b><small className={`status-${template.calibration_status}`}>{calibrationLabel(template)}</small></span>
                  {selectedTemplateId === template.id && <i>本批次</i>}
                </button>
              ))}
              {!visibleTemplates.length && <p className="pod-template-empty">{scope === "personal" ? "还没有个人模板，请先上传。" : "暂无系统模板。"}</p>}
            </div>
          </section>

          <section className="pod-template-calibration-panel">
            {activeTemplate ? (
              <>
                <div className="pod-template-panel-title">
                  <div><span>MASK &amp; ANCHOR</span><h3>{activeTemplate.name}</h3></div>
                  <span className={`pod-calibration-status status-${activeTemplate.calibration_status}`}>{calibrationLabel(activeTemplate)}</span>
                </div>
                <TemplateCalibrationCanvas
                  template={activeTemplate}
                  calibration={calibration}
                  disabled={activeTemplate.source === "system"}
                  onChange={setCalibration}
                />
                {activeTemplate.error_message && <p className="pod-template-error">{activeTemplate.error_message}</p>}
                <div className="pod-template-calibration-actions">
                  {activeTemplate.source === "personal" && <>
                    <button type="button" disabled={Boolean(busyAction)} onClick={() => void onCalibrate(activeTemplate.id).catch(() => undefined)}><span className="iconfont icon-robot" /> AI 自动标定</button>
                    <button type="button" className="pod-primary-button" disabled={Boolean(busyAction)} onClick={() => void onSaveCalibration(activeTemplate.id, calibration).catch(() => undefined)}>保存微调</button>
                  </>}
                  <button type="button" className={activeTemplate.calibration_status === "ready" ? "pod-primary-button" : ""} disabled={activeTemplate.calibration_status !== "ready"} onClick={() => { onSelect(activeTemplate.id); onClose(); }}>用于本批次</button>
                </div>
              </>
            ) : (
              <div className="pod-template-empty pod-template-empty-large"><span className="iconfont icon-skin" /><p>选择模板后在这里查看标定。</p></div>
            )}
          </section>
        </div>
      </aside>
    </div>
  );
}
