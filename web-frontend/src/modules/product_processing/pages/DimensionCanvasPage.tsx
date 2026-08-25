import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  completeDimensionItem,
  getDimensionBatch,
  getDimensionItem,
  listDimensionBatches,
  retryDimensionRender,
  saveDimensionItem,
  submitDimensionBatchReview,
  uploadDimensionAsset,
} from "../api/dimensionCanvasApi";
import { DimensionCanvasImportDialog } from "../components/DimensionCanvasImportDialog";
import { DimensionCanvasQueue } from "../components/DimensionCanvasQueue";
import { DimensionCanvasStage } from "../components/DimensionCanvasStage";
import { DimensionCanvasToolbar } from "../components/DimensionCanvasToolbar";
import {
  addAnnotation,
  canComplete,
  centimetersToUnit,
  changeDisplayUnit,
  changeDimensionValue,
  invalidateRenderOnEdit,
  nextQueueItem,
  removeAnnotation,
  unitToCentimeters,
} from "../data/dimensionCanvasModel";
import { useDimensionCanvasAutosave } from "../hooks/useDimensionCanvasAutosave";
import type {
  DimensionAsset,
  DimensionCanvasBatch,
  DimensionCanvasItem,
  DimensionKey,
  DimensionUnit,
  EditorState,
  NormalizedPoint,
  SaveDimensionItemRequest,
} from "../types/dimensionCanvas";
import "../styles/dimension-canvas.css";

type Props = {
  initialBatchId?: string;
  initialItemId?: string;
  onOpenPrecheck: (taskId: number, changeSetId?: string) => void;
  isActive?: boolean;
};

type HistoryState = {
  past: EditorState[];
  future: EditorState[];
};

const DIMENSION_LABELS = { length: "长", width: "宽", height: "高" } as const;
const UNIT_OPTIONS: Array<{ value: DimensionUnit; label: string }> = [
  { value: "cm", label: "厘米 (cm)" },
  { value: "mm", label: "毫米 (mm)" },
  { value: "in", label: "英寸 (in)" },
  { value: "ft", label: "英尺 (ft)" },
];

function CanvasAssetThumb({ asset }: { asset: DimensionAsset }) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [asset.previewUrl]);

  if (failed) return <span className="dimension-asset-thumb-failed">加载失败</span>;
  if (!asset.previewUrl) return <span>待加载</span>;
  return (
    <img
      src={asset.previewUrl}
      alt={asset.role}
      draggable={false}
      onDragStart={(event) => event.preventDefault()}
      onError={() => setFailed(true)}
    />
  );
}

export function DimensionCanvasPage({ initialBatchId, initialItemId, onOpenPrecheck, isActive = true }: Props) {
  const [batches, setBatches] = useState<DimensionCanvasBatch[]>([]);
  const [batch, setBatch] = useState<DimensionCanvasBatch | null>(null);
  const [activeItemId, setActiveItemId] = useState(initialItemId ?? "");
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [editorItemId, setEditorItemId] = useState("");
  const [history, setHistory] = useState<HistoryState>({ past: [], future: [] });
  const [zoom, setZoom] = useState(1);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const locallyEditedIds = useRef(new Set<string>());
  const renderWatchGeneration = useRef(new Map<string, number>());

  useEffect(() => {
    if (!isActive) setImportOpen(false);
  }, [isActive]);

  const currentItem = useMemo(
    () => batch?.items.find((item) => item.id === activeItemId) ?? null,
    [activeItemId, batch],
  );

  const loadBatches = useCallback(async () => {
    try {
      setBatches(await listDimensionBatches());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  const openBatch = useCallback(async (batchId: string, preferredItemId = "") => {
    setLoading(true);
    setError("");
    try {
      const data = await getDimensionBatch(batchId);
      setBatch(data);
      const target = data.items.find((item) => item.id === preferredItemId) ?? data.items[0] ?? null;
      setActiveItemId(target?.id ?? "");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadBatches();
    if (initialBatchId) void openBatch(initialBatchId, initialItemId);
    else if (initialItemId) {
      setLoading(true);
      getDimensionItem(initialItemId)
        .then((item) => {
          setBatch({
            id: item.batchId,
            sourceTaskId: item.taskId,
            status: "editing",
            totalCount: 1,
            completedCount: item.state === "completed" ? 1 : 0,
            failedCount: 0,
            items: [item],
          });
          setActiveItemId(item.id);
        })
        .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))
        .finally(() => setLoading(false));
    }
  }, [initialBatchId, initialItemId, loadBatches, openBatch]);

  useEffect(() => {
    if (!currentItem) {
      setEditor(null);
      setEditorItemId("");
      return;
    }
    setEditor(currentItem.editor);
    setEditorItemId(currentItem.id);
    setHistory({ past: [], future: [] });
    setZoom(1);
    setError("");
  }, [currentItem?.id]);

  useEffect(() => {
    if (!currentItem) return;
    let cancelled = false;
    const baselineRevision = currentItem.sourcePreviewRevision;
    getDimensionItem(currentItem.id)
      .then((remote) => {
        if (cancelled) return;
        if (remote.sourcePreviewRevision !== baselineRevision && locallyEditedIds.current.has(remote.id)) {
          setError("上游预检版本已变化；当前商品已有画布编辑，本地内容已保留且未被静默覆盖。请核对冲突。" );
          return;
        }
        if (!locallyEditedIds.current.has(remote.id)) {
          setBatch((current) => current ? {
            ...current,
            items: current.items.map((item) => item.id === remote.id ? remote : item),
          } : current);
          setEditor(remote.editor);
          setEditorItemId(remote.id);
        }
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [currentItem?.id]);

  const saveCurrent = useCallback(
    (request: SaveDimensionItemRequest) => {
      if (!currentItem) return Promise.reject(new Error("没有可保存的画布项目"));
      return saveDimensionItem(currentItem.id, request);
    },
    [currentItem?.id],
  );

  const autosaveEditor = editor && editorItemId === currentItem?.id
    ? editor
    : currentItem?.editor ?? nullEditor();
  const autosave = useDimensionCanvasAutosave(currentItem, autosaveEditor, saveCurrent);

  const watchRender = useCallback(async (itemId: string) => {
    const generation = (renderWatchGeneration.current.get(itemId) ?? 0) + 1;
    renderWatchGeneration.current.set(itemId, generation);
    for (let attempt = 0; attempt < 80; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 500));
      if (renderWatchGeneration.current.get(itemId) !== generation) return;
      try {
        const fresh = await getDimensionItem(itemId);
        setBatch((current) => current ? {
          ...current,
          items: current.items.map((item) => item.id === fresh.id ? fresh : item),
        } : current);
        if (fresh.state !== "rendering") return;
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
        return;
      }
    }
    setError("尺寸图仍在渲染，可稍后点击刷新历史批次查看结果");
  }, []);

  useEffect(() => {
    if (!autosave.savedItem) return;
    setBatch((current) => current ? {
      ...current,
      items: current.items.map((item) => item.id === autosave.savedItem?.id ? {
        ...item,
        itemRevision: autosave.savedItem.itemRevision,
        sourcePreviewRevision: autosave.savedItem.sourcePreviewRevision,
      } : item),
    } : current);
  }, [autosave.savedItem]);

  useEffect(() => {
    if (!batch || !activeItemId) return;
    const index = batch.items.findIndex((item) => item.id === activeItemId);
    const next = batch.items[index + 1];
    if (!next) return;
    const url = next.assets.find((asset) => asset.previewUrl)?.previewUrl;
    if (!url) return;
    const image = new Image();
    image.src = url;
  }, [activeItemId, batch]);

  const updateEditor = (next: EditorState, recordHistory = true, invalidatesRender = true) => {
    if (!editor || editorItemId !== activeItemId) return;
    if (recordHistory) setHistory((current) => ({ past: [...current.past, editor], future: [] }));
    setEditor(next);
    if (invalidatesRender) {
      locallyEditedIds.current.add(activeItemId);
      setBatch((current) => current ? {
        ...current,
        items: current.items.map((item) => item.id === activeItemId ? invalidateRenderOnEdit(item, next) : item),
      } : current);
    }
  };

  const switchItem = (direction: -1 | 1) => {
    if (!batch || !currentItem) return;
    if (autosave.state === "saving") {
      setMessage("当前修改正在保存，完成后再切换商品");
      return;
    }
    if (autosave.state === "error") {
      setError(autosave.error || "保存失败，请重试后再切换商品");
      return;
    }
    const nextId = nextQueueItem(batch.items.map((item) => item.id), currentItem.id, direction);
    if (nextId && nextId !== currentItem.id) {
      setEditor(null);
      setEditorItemId("");
      setActiveItemId(nextId);
    }
  };

  const selectItem = (itemId: string) => {
    if (itemId === activeItemId) return;
    if (autosave.state === "saving" || autosave.state === "error") {
      setError(autosave.error || "请等待当前商品保存完成后再切换");
      return;
    }
    setEditor(null);
    setEditorItemId("");
    setActiveItemId(itemId);
  };

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement) return;
      if (event.key === "ArrowLeft") switchItem(-1);
      if (event.key === "ArrowRight") switchItem(1);
      if ((event.key === "Delete" || event.key === "Backspace") && editor?.selectedAnnotationId) {
        event.preventDefault();
        updateEditor(removeAnnotation(editor, editor.selectedAnnotationId));
      }
    };
    root.addEventListener("keydown", handleKey);
    return () => root.removeEventListener("keydown", handleKey);
  }, [batch, currentItem, editor, autosave.state]);

  const commitAnnotation = (start: NormalizedPoint, end: NormalizedPoint) => {
    if (!editor || editor.activeTool === "select") return;
    updateEditor(addAnnotation(editor, editor.activeTool, start, end));
  };

  const selectDimensionTool = (key: Exclude<DimensionKey, "custom">) => {
    if (!editor) return;
    const dimension = editor.dimensions[key];
    if (dimension.valueCm == null || dimension.valueCm <= 0) {
      setError(`请先填写${DIMENSION_LABELS[key]}的数值`);
      return;
    }
    const confirmed = new Set(["source_confirmed", "manual_confirmed"]).has(dimension.provenance);
    const next = confirmed ? editor : changeDimensionValue(editor, key, dimension.valueCm);
    updateEditor({ ...next, activeTool: key, selectedAnnotationId: null }, !confirmed, !confirmed);
    setMessage(confirmed ? `已选择“${DIMENSION_LABELS[key]}”，请在图上拖出尺寸线` : `已确认“${DIMENSION_LABELS[key]}”并进入绘制`);
  };

  const uploadAsset = async (file: File | null) => {
    if (!file || !currentItem || !editor) return;
    if (autosave.state === "saving") {
      setMessage("当前修改保存完成后再上传图片");
      return;
    }
    setBusy("upload");
    setError("");
    try {
      const uploaded = await uploadDimensionAsset(currentItem.id, file);
      const nextEditor = { ...editor, selectedAssetId: uploaded.assetId };
      setHistory((current) => ({ past: [...current.past, editor], future: [] }));
      locallyEditedIds.current.add(currentItem.id);
      setEditor(nextEditor);
      setBatch((current) => current ? {
        ...current,
        items: current.items.map((item) => item.id === uploaded.item.id
          ? invalidateRenderOnEdit(uploaded.item, nextEditor)
          : item),
      } : current);
      setMessage("图片已导入并选中，可直接绘制尺寸线");
    } catch (cause) {
      setError(`图片导入失败：${cause instanceof Error ? cause.message : String(cause)}`);
    } finally {
      setBusy("");
      if (uploadInputRef.current) uploadInputRef.current.value = "";
    }
  };

  const undo = () => {
    if (!editor || history.past.length === 0) return;
    const previous = history.past[history.past.length - 1];
    setHistory({ past: history.past.slice(0, -1), future: [editor, ...history.future] });
    updateEditor(previous, false);
  };

  const redo = () => {
    if (!editor || history.future.length === 0) return;
    const next = history.future[0];
    setHistory({ past: [...history.past, editor], future: history.future.slice(1) });
    updateEditor(next, false);
  };

  const complete = async () => {
    if (!currentItem || !editor) return;
    const gate = canComplete(editor);
    if (!gate.ok) { setError(gate.reason); return; }
    if (autosave.state === "saving") { setError("请等待自动保存完成后再提交渲染"); return; }
    if (autosave.state === "error") { setError(autosave.error); return; }
    setBusy("complete");
    setError("");
    try {
      const fresh = await getDimensionItem(currentItem.id);
      if (fresh.sourcePreviewRevision !== currentItem.sourcePreviewRevision && history.past.length > 0) {
        setError("预检版本已变化，本地已编辑内容不会被覆盖。请返回预检核对后再完成。" );
        return;
      }
      const result = await completeDimensionItem(fresh.id, fresh.itemRevision);
      setBatch((current) => current ? { ...current, items: current.items.map((item) => item.id === result.id ? result : item) } : current);
      void watchRender(result.id);
      setMessage("已提交本地确定性渲染，可继续下一条");
      switchItem(1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const submitReview = async () => {
    if (!batch) return;
    setBusy("review");
    setError("");
    try {
      const changeSet = await submitDimensionBatchReview(batch.id);
      window.dispatchEvent(new CustomEvent("mainpg:dimension-change-set", { detail: { changeSetId: changeSet.id } }));
      setMessage(`已交回 ${changeSet.itemCount} 项审核；未完成项目继续保留在当前批次`);
      onOpenPrecheck(changeSet.sourceTaskId || batch.sourceTaskId, changeSet.id);
    } catch (cause) {
      setError(`交回审核失败：${cause instanceof Error ? cause.message : String(cause)}`);
    } finally {
      setBusy("");
    }
  };

  const retryRender = async (itemId: string) => {
    setBusy(itemId);
    try {
      const item = batch?.items.find((candidate) => candidate.id === itemId);
      if (!item) throw new Error("找不到待重试的画布项目");
      const result = await retryDimensionRender(itemId, item.itemRevision);
      setBatch((current) => current ? { ...current, items: current.items.map((item) => item.id === result.id ? result : item) } : current);
      void watchRender(result.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const activeAsset = editor
    ? currentItem?.assets.find((asset) => asset.id === editor.selectedAssetId) ?? null
    : null;
  const completion = editor ? canComplete(editor) : { ok: false, reason: "请选择商品" };

  return (
    <div className="dimension-page" ref={rootRef} tabIndex={-1}>
      <header className="dimension-commandbar">
        <div className="dimension-command-copy">
          <div className="dimension-title-row">
            <span className="dimension-title-icon iconfont icon-column-width" aria-hidden="true" />
            <h1>尺寸画布</h1>
          </div>
          <p>商品本体尺寸与箭头由结构化数据绘制；物流包裹尺寸不会进入画布。</p>
        </div>
        <div className="dimension-command-actions">
          <button onClick={() => setImportOpen(true)}><i className="iconfont icon-upload" aria-hidden="true" />导入已完成任务</button>
          <button onClick={() => void loadBatches()}><i className="iconfont icon-sync" aria-hidden="true" />刷新历史批次</button>
          {batch && <button className="primary" onClick={submitReview} disabled={busy !== "" || !batch.items.some((item) => item.state === "completed")}><i className="iconfont icon-check-circle" aria-hidden="true" />{busy === "review" ? "交回中…" : "交回审核"}</button>}
        </div>
      </header>

      {(error || autosave.error) && <div className="dimension-banner is-error">{error || `画布草稿保存失败：${autosave.error}`}{autosave.state === "error" && autosave.retryable && <button onClick={autosave.retry}>重试保存</button>}</div>}
      {message && <div className="dimension-banner is-success">{message}</div>}
      {autosave.conflictItem && <div className="dimension-banner is-warning">预检基线或画布版本已更新。本地编辑已保留，远端版本 #{autosave.conflictItem.itemRevision} 未覆盖当前画布。</div>}

      {!batch ? (
        <div className="dimension-landing-grid">
          <section className="dimension-empty-card">
            <span className="dimension-empty-icon iconfont icon-column-width" aria-hidden="true" />
            <h2>{loading ? "正在加载尺寸画布…" : "从单商品或批量任务开始"}</h2>
            <p>在预检商品卡点击“添加尺寸图”，或导入已完成任务。页面刷新后草稿仍可继续。</p>
            <button className="primary" onClick={() => setImportOpen(true)}><i className="iconfont icon-upload" aria-hidden="true" />导入已完成任务</button>
          </section>
          <section className="dimension-history">
            <header><h2>历史批次</h2><span>{batches.length} 个</span></header>
            {batches.length === 0 ? <p>暂无历史尺寸画布批次</p> : batches.map((item) => (
              <button key={item.id} onClick={() => void openBatch(item.id)}>
                <span><strong>任务 #{item.sourceTaskId}</strong><small>{item.status}</small></span>
                <span>{item.completedCount}/{item.totalCount} 完成</span>
              </button>
            ))}
          </section>
        </div>
      ) : editor && currentItem ? (
        <>
          <section className="dimension-meta-strip">
            <div className="dimension-assets">
              <span className="dimension-meta-label">素材图</span>
              <div className="dimension-asset-list">
                {currentItem.assets.map((asset) => (
                  <button key={asset.id} className={editor.selectedAssetId === asset.id ? "is-active" : ""} onClick={() => updateEditor({ ...editor, selectedAssetId: asset.id })} disabled={asset.availability === "failed"}>
                    {asset.previewUrl ? <CanvasAssetThumb asset={asset} /> : <span>待加载</span>}
                    <small>{asset.role}</small>
                  </button>
                ))}
                <button className="dimension-upload-tile" onClick={() => uploadInputRef.current?.click()} disabled={busy !== "" || autosave.state === "saving"}>
                  <span aria-hidden="true">＋</span>
                  <small>{busy === "upload" ? "导入中…" : "导入图片"}</small>
                </button>
                <input
                  ref={uploadInputRef}
                  className="dimension-upload-input"
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  onChange={(event) => void uploadAsset(event.target.files?.[0] ?? null)}
                />
              </div>
            </div>
            <div className="dimension-values">
              <div className="dimension-value-heading">
                <span className="dimension-meta-label">商品本体尺寸</span>
                <label>显示单位
                  <select value={editor.displayUnit} onChange={(event) => updateEditor(changeDisplayUnit(editor, event.target.value as DimensionUnit))}>
                    {UNIT_OPTIONS.map((unit) => <option key={unit.value} value={unit.value}>{unit.label}</option>)}
                  </select>
                </label>
              </div>
              <div className="dimension-value-list">
                {(["length", "width", "height"] as const).map((key) => {
                  const value = editor.dimensions[key];
                  return (
                    <label key={key} className={`provenance-${value.provenance}`}>
                      <span>{DIMENSION_LABELS[key]}</span>
                      <input type="number" min="0" step={editor.displayUnit === "mm" ? "1" : "0.01"} value={value.valueCm == null ? "" : Number(centimetersToUnit(value.valueCm, editor.displayUnit).toFixed(editor.displayUnit === "mm" ? 1 : 2))} onChange={(event) => {
                        const parsed = Number(event.target.value);
                        if (event.target.value === "") {
                          updateEditor({ ...editor, dimensions: { ...editor.dimensions, [key]: { valueCm: null, provenance: "unconfirmed", evidenceRef: "manual" } } });
                        } else if (Number.isFinite(parsed) && parsed > 0) {
                          updateEditor(changeDimensionValue(editor, key, unitToCentimeters(parsed, editor.displayUnit)));
                        }
                      }} />
                      <button type="button" onClick={() => selectDimensionTool(key)} disabled={value.valueCm == null || value.valueCm <= 0}>绘制</button>
                      <em>{editor.displayUnit} · {value.provenance === "package_estimate" ? "处理表估值（绘制前确认）" : value.provenance}</em>
                    </label>
                  );
                })}
              </div>
            </div>
          </section>
          <div className="dimension-editor-layout">
            <DimensionCanvasToolbar
              editor={editor}
              canUndo={history.past.length > 0}
              canRedo={history.future.length > 0}
              onTool={(activeTool: DimensionKey | "select") => {
                if (activeTool === "select" || activeTool === "custom") {
                  updateEditor({ ...editor, activeTool, selectedAnnotationId: activeTool === "select" ? editor.selectedAnnotationId : null }, false, false);
                } else {
                  selectDimensionTool(activeTool);
                }
              }}
              onUndo={undo}
              onRedo={redo}
              onDelete={() => editor.selectedAnnotationId && updateEditor(removeAnnotation(editor, editor.selectedAnnotationId))}
              onFit={() => setZoom(1)}
              onZoomIn={() => setZoom((value) => Math.min(2.5, value + 0.15))}
              onZoomOut={() => setZoom((value) => Math.max(0.5, value - 0.15))}
              onReset={() => setZoom(1)}
              onStyle={(style) => updateEditor({ ...editor, annotations: editor.annotations.map((annotation) => editor.selectedAnnotationId === annotation.id ? { ...annotation, style } : annotation) })}
              onLineWidth={(lineWidth) => updateEditor({ ...editor, annotations: editor.annotations.map((annotation) => editor.selectedAnnotationId === annotation.id ? { ...annotation, lineWidth } : annotation) })}
              onEndpointStyle={(endpointStyle) => {
                const changesSelected = Boolean(editor.selectedAnnotationId);
                updateEditor({
                  ...editor,
                  endpointStyle,
                  annotations: editor.annotations.map((annotation) => editor.selectedAnnotationId === annotation.id
                    ? { ...annotation, endpointStyle }
                    : annotation),
                }, changesSelected, changesSelected);
              }}
              onCustomValueChange={(customValueCm) => updateEditor({ ...editor, customValueCm })}
            />
            <main className="dimension-stage-panel">
              <DimensionCanvasStage
                editor={editor}
                asset={activeAsset}
                zoom={zoom}
                onZoomChange={setZoom}
                onSelectAnnotation={(annotationId) => updateEditor({ ...editor, selectedAnnotationId: annotationId }, false, false)}
                onCommitEditor={(next) => updateEditor(next)}
                onCommitAnnotation={commitAnnotation}
              />
              <footer className="dimension-editor-footer">
                <label>回写位置
                  <select value={editor.targetSlotId} onChange={(event) => updateEditor({ ...editor, targetSlotId: event.target.value })}>
                    <option value="carousel.dimension_background">轮播尺寸槽位</option>
                  </select>
                </label>
                <div className={`dimension-autosave state-${autosave.state}`}><span />{autosave.state === "saving" ? "自动保存中" : autosave.state === "saved" ? "已自动保存" : autosave.state === "error" ? "保存失败" : "等待编辑"}</div>
                <div className="dimension-complete">
                  {!completion.ok && <span>{completion.reason}</span>}
                  <button className="primary" disabled={!completion.ok || busy !== "" || autosave.state === "saving" || autosave.state === "error"} onClick={complete}>{busy === "complete" ? "提交中…" : "完成并下一条"}</button>
                </div>
              </footer>
            </main>
            <DimensionCanvasQueue items={batch.items} activeItemId={activeItemId} onSelect={selectItem} onPrevious={() => switchItem(-1)} onNext={() => switchItem(1)} onRetryRender={retryRender} />
          </div>
        </>
      ) : <p className="dimension-empty">此批次没有可编辑项目。</p>}

      <DimensionCanvasImportDialog open={importOpen} onClose={() => setImportOpen(false)} onImported={(data) => {
        setBatch(data);
        setBatches((current) => [data, ...current.filter((item) => item.id !== data.id)]);
        setEditor(null);
        setEditorItemId("");
        setActiveItemId(data.items[0]?.id ?? "");
      }} />
    </div>
  );
}

function nullEditor(): EditorState {
  return {
    selectedAssetId: "",
    targetSlotId: "",
    dimensions: {
      length: { valueCm: null, provenance: "unconfirmed", evidenceRef: "" },
      width: { valueCm: null, provenance: "unconfirmed", evidenceRef: "" },
      height: { valueCm: null, provenance: "unconfirmed", evidenceRef: "" },
      conflict: false,
    },
    annotations: [],
    activeTool: "select",
    selectedAnnotationId: null,
    displayUnit: "cm",
    customValueCm: null,
    endpointStyle: "arrow",
  };
}

export default DimensionCanvasPage;
