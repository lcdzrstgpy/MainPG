import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import type { PodSystemTemplate } from "../data/podCustomizationDraft";
import { defaultTemplateCalibration } from "../data/podCustomizationModel";
import { PodAssetImage } from "../data/usePodAssetUrl";
import type { PodTemplate, PodTemplateCalibration } from "../types";
import { TemplateCalibrationCanvas } from "./TemplateCalibrationCanvas";

type Props = {
  open: boolean;
  templates: PodTemplate[];
  systemTemplates?: PodSystemTemplate[];
  selectedTemplateId?: string;
  busyAction: string;
  onClose: () => void;
  onSelect: (templateId: string) => void;
  onApplySystemTemplate?: (template: PodSystemTemplate) => void;
  onDeleteSystemTemplate?: (templateId: string) => void;
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

function savedAtLabel(createdAt: string): string {
  const savedAt = new Date(createdAt);
  if (Number.isNaN(savedAt.getTime())) return "已保存";
  return `保存于 ${savedAt.toLocaleString("zh-CN", { dateStyle: "short", timeStyle: "short" })}`;
}

export function TemplateLibraryDrawer({
  open,
  templates,
  systemTemplates = [],
  selectedTemplateId,
  busyAction,
  onClose,
  onSelect,
  onApplySystemTemplate,
  onDeleteSystemTemplate,
  onUpload,
  onCalibrate,
  onSaveCalibration,
}: Props) {
  const [scope, setScope] = useState<"system" | "personal">("system");
  const [activePersonalTemplateId, setActivePersonalTemplateId] = useState("");
  const [activeSystemTemplateId, setActiveSystemTemplateId] = useState("");
  const [calibration, setCalibration] = useState<PodTemplateCalibration>(defaultTemplateCalibration());
  const [uploadName, setUploadName] = useState("");
  const [uploadFile, setUploadFile] = useState<File>();
  const fileRef = useRef<HTMLInputElement>(null);

  const personalTemplates = useMemo(
    () => templates.filter((template) => template.source === "personal"),
    [templates],
  );
  const activePersonalTemplate = personalTemplates.find((template) => template.id === activePersonalTemplateId)
    ?? personalTemplates.find((template) => template.id === selectedTemplateId)
    ?? personalTemplates[0];
  const activeSystemTemplate = systemTemplates.find((template) => template.id === activeSystemTemplateId)
    ?? systemTemplates[0];
  const activeTemplate = scope === "system" ? activeSystemTemplate?.template : activePersonalTemplate;
  const linkedTemplateAvailable = activeSystemTemplate
    ? templates.some((template) => template.id === activeSystemTemplate.templateId)
    : false;
  const systemTemplateActionsAvailable = Boolean(onApplySystemTemplate && onDeleteSystemTemplate);

  useEffect(() => {
    if (!open) return;
    const selected = templates.find((template) => template.id === selectedTemplateId);
    if (selected?.source === "personal") {
      setScope("personal");
      setActivePersonalTemplateId(selected.id);
    } else {
      setScope("system");
      setActiveSystemTemplateId((current) => systemTemplates.some((template) => template.id === current) ? current : systemTemplates[0]?.id ?? "");
    }
  }, [open, selectedTemplateId, systemTemplates, templates]);

  useEffect(() => {
    setCalibration(activeTemplate?.calibration ?? defaultTemplateCalibration());
  }, [activeTemplate?.id, activeTemplate?.updated_at, activeSystemTemplate?.id]);

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
    if (nextScope === "system") {
      setActiveSystemTemplateId((current) => systemTemplates.some((template) => template.id === current) ? current : systemTemplates[0]?.id ?? "");
      return;
    }
    setActivePersonalTemplateId((current) => personalTemplates.some((template) => template.id === current) ? current : personalTemplates[0]?.id ?? "");
  };

  const submitUpload = async (event: FormEvent) => {
    event.preventDefault();
    if (!uploadFile || busyAction) return;
    try {
      const created = await onUpload(uploadFile, uploadName);
      setScope("personal");
      setActivePersonalTemplateId(created.id);
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
              {scope === "system" && systemTemplates.map((template) => (
                <button
                  key={template.id}
                  type="button"
                  className={`pod-template-card pod-system-template-card ${activeSystemTemplate?.id === template.id ? "is-active" : ""}`}
                  onClick={() => setActiveSystemTemplateId(template.id)}
                >
                  <PodAssetImage path={template.template.preview_url || template.template.original_url} alt="" loading="lazy" />
                  <span><b>{template.name}</b><small>{savedAtLabel(template.createdAt)}</small></span>
                </button>
              ))}
              {scope === "personal" && personalTemplates.map((template) => (
                <button
                  key={template.id}
                  type="button"
                  className={`pod-template-card ${activePersonalTemplate?.id === template.id ? "is-active" : ""} ${selectedTemplateId === template.id ? "is-selected" : ""}`}
                  onClick={() => {
                    setActivePersonalTemplateId(template.id);
                    onSelect(template.id);
                  }}
                >
                  <PodAssetImage path={template.preview_url || template.original_url} alt="" loading="lazy" />
                  <span><b>{template.name}</b><small className={`status-${template.calibration_status}`}>{calibrationLabel(template)}</small></span>
                  {selectedTemplateId === template.id && <i>本批次</i>}
                </button>
              ))}
              {scope === "system" && !systemTemplates.length && <p className="pod-template-empty">暂无系统模板。可在主页面保存当前提示词与图片模板。</p>}
              {scope === "personal" && !personalTemplates.length && <p className="pod-template-empty">还没有个人模板，请先上传。</p>}
            </div>
          </section>

          <section className="pod-template-calibration-panel">
            {activeTemplate ? (
              <>
                <div className="pod-template-panel-title">
                  <div><span>{scope === "system" ? "SAVED PROMPT &amp; SNAPSHOT" : "MASK & ANCHOR"}</span><h3>{scope === "system" ? activeSystemTemplate?.name : activeTemplate.name}</h3></div>
                  {scope === "personal" && <span className={`pod-calibration-status status-${activeTemplate.calibration_status}`}>{calibrationLabel(activeTemplate)}</span>}
                  {scope === "system" && <span className="pod-calibration-status status-ready">图片快照</span>}
                </div>
                <TemplateCalibrationCanvas
                  template={activeTemplate}
                  calibration={calibration}
                  disabled={scope === "system"}
                  onChange={scope === "system" ? () => undefined : setCalibration}
                />
                {scope === "system" && activeSystemTemplate && <details className="pod-system-template-prompt"><summary>查看已保存提示词</summary><pre>{activeSystemTemplate.creativePrompt}</pre></details>}
                {scope === "personal" && activeTemplate.error_message && <p className="pod-template-error">{activeTemplate.error_message}</p>}
                <div className="pod-template-calibration-actions">
                  {scope === "personal" && <>
                    <button type="button" disabled={Boolean(busyAction)} onClick={() => void onCalibrate(activeTemplate.id).catch(() => undefined)}><span className="iconfont icon-robot" /> AI 自动标定</button>
                    <button type="button" className="pod-primary-button" disabled={Boolean(busyAction)} onClick={() => void onSaveCalibration(activeTemplate.id, calibration).catch(() => undefined)}>保存微调</button>
                  </>}
                  {scope === "system" && activeSystemTemplate ? <>
                    {(!linkedTemplateAvailable || !systemTemplateActionsAvailable) && <p className="pod-system-template-unavailable">{linkedTemplateAvailable ? "系统模板操作暂不可用。" : "系统模板绑定的图片已不可用，无法用于本批次。"}</p>}
                    <button type="button" disabled={!onDeleteSystemTemplate} onClick={() => { onDeleteSystemTemplate?.(activeSystemTemplate.id); setActiveSystemTemplateId(""); }}>删除模板</button>
                    <button type="button" className="pod-primary-button" disabled={!linkedTemplateAvailable || !systemTemplateActionsAvailable} onClick={() => { if (!linkedTemplateAvailable || !onApplySystemTemplate) return; onApplySystemTemplate(activeSystemTemplate); onClose(); }}>用于本批次</button>
                  </> : <button type="button" className={activeTemplate.calibration_status === "ready" ? "pod-primary-button" : ""} disabled={activeTemplate.calibration_status !== "ready"} onClick={() => { onSelect(activeTemplate.id); onClose(); }}>用于本批次</button>}
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
