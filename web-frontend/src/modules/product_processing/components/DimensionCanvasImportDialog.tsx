import { useEffect, useRef, useState } from "react";

import {
  getDimensionTaskEligibility,
  importDimensionTask,
  listImportableDimensionTasks,
} from "../api/dimensionCanvasApi";
import type {
  DimensionCanvasBatch,
  DimensionEligibilityItem,
  DimensionTaskEligibility,
  ImportableDimensionTask,
} from "../types/dimensionCanvas";

type Props = {
  open: boolean;
  onClose: () => void;
  onImported: (batch: DimensionCanvasBatch) => void;
};

type ExistingAction = "keep" | "remake" | "skip";

const EMPTY_ELIGIBILITY: DimensionTaskEligibility = {
  ready: [],
  needsDimensions: [],
  existingDimension: [],
  assetFailed: [],
};

export function DimensionCanvasImportDialog({ open, onClose, onImported }: Props) {
  const [tasks, setTasks] = useState<ImportableDimensionTask[]>([]);
  const [taskId, setTaskId] = useState<number | null>(null);
  const [eligibility, setEligibility] = useState(EMPTY_ELIGIBILITY);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [existingActions, setExistingActions] = useState<Record<string, ExistingAction>>({});
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const eligibilityCache = useRef(new Map<number, DimensionTaskEligibility>());

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError("");
    listImportableDimensionTasks()
      .then((data) => {
        setTasks(data);
        if (data.length && taskId == null) setTaskId(data[0].taskId);
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))
      .finally(() => setLoading(false));
  }, [open]);

  useEffect(() => {
    if (!open || taskId == null) return;
    const cached = eligibilityCache.current.get(taskId);
    const apply = (data: DimensionTaskEligibility) => {
      setEligibility(data);
      setSelected(new Set(data.ready.map((item) => item.taskItemId)));
      setExistingActions(Object.fromEntries(data.existingDimension.map((item) => [String(item.taskItemId), "keep"])));
    };
    if (cached) {
      apply(cached);
      return;
    }
    setLoading(true);
    getDimensionTaskEligibility(taskId)
      .then((data) => {
        eligibilityCache.current.set(taskId, data);
        apply(data);
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))
      .finally(() => setLoading(false));
  }, [open, taskId]);

  const toggle = (id: number) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const orderedItems = [
    ...eligibility.ready,
    ...eligibility.needsDimensions,
    ...eligibility.existingDimension,
    ...eligibility.assetFailed,
  ];

  const submit = async () => {
    if (taskId == null || selected.size === 0) return;
    setSubmitting(true);
    setError("");
    try {
      const taskItemIds = orderedItems
        .map((item) => item.taskItemId)
        .filter((id) => selected.has(id));
      const batch = await importDimensionTask({
        task_id: taskId,
        task_item_ids: taskItemIds,
        existing_dimension_actions: existingActions,
      });
      onImported(batch);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  const group = (title: string, hint: string, items: DimensionEligibilityItem[], existing = false) => (
    <section className="dimension-import-group">
      <header><strong>{title}</strong><span>{items.length} 项 · {hint}</span></header>
      {items.length === 0 ? <p>暂无商品</p> : items.map((item) => (
        <div key={item.taskItemId} className="dimension-import-item">
          <label>
            <input type="checkbox" checked={selected.has(item.taskItemId)} onChange={() => toggle(item.taskItemId)} />
            <span><strong>{item.skc || `商品 #${item.taskItemId}`}</strong><small>{item.label}</small></span>
          </label>
          {existing && (
            <select
              value={existingActions[String(item.taskItemId)] ?? "keep"}
              onChange={(event) => setExistingActions((current) => ({
                ...current,
                [String(item.taskItemId)]: event.target.value as ExistingAction,
              }))}
            >
              <option value="keep">保留</option>
              <option value="remake">重做</option>
              <option value="skip">跳过</option>
            </select>
          )}
        </div>
      ))}
    </section>
  );

  return (
    <div className="dimension-modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <div className="dimension-modal" role="dialog" aria-modal="true" aria-labelledby="dimension-import-title">
        <header className="dimension-modal-head">
          <div><span>批量工作流</span><h2 id="dimension-import-title">导入已完成任务</h2></div>
          <button onClick={onClose} aria-label="关闭">×</button>
        </header>
        <label className="dimension-task-select">选择任务
          <select value={taskId ?? ""} onChange={(event) => setTaskId(Number(event.target.value))}>
            {tasks.map((task) => <option key={task.taskId} value={task.taskId}>{task.title || `任务 #${task.taskId}`}（{task.itemCount} 项）</option>)}
          </select>
        </label>
        {error && <div className="dimension-banner is-error">{error}</div>}
        {loading ? <p className="dimension-empty">正在读取可导入商品…</p> : (
          <div className="dimension-import-groups">
            {group("可直接制作", "默认选中", eligibility.ready)}
            {group("待补尺寸", "导入后需人工确认", eligibility.needsDimensions)}
            {group("已有尺寸图", "请选择保留、重做或跳过", eligibility.existingDimension, true)}
            {group("素材不可用", "可稍后重选或上传", eligibility.assetFailed)}
          </div>
        )}
        <footer className="dimension-modal-actions">
          <span>已选 {selected.size} 项</span>
          <button onClick={onClose}>取消</button>
          <button className="primary" onClick={submit} disabled={submitting || selected.size === 0}>{submitting ? "导入中…" : "导入队列"}</button>
        </footer>
      </div>
    </div>
  );
}
