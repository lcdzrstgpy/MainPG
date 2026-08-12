import type {
  DimensionAnnotation,
  DimensionCanvasItem,
  DimensionKey,
  EditorState,
  NormalizedPoint,
} from "../types/dimensionCanvas.ts";

export function clampPoint(point: NormalizedPoint): NormalizedPoint {
  return {
    x: Math.min(1, Math.max(0, point.x)),
    y: Math.min(1, Math.max(0, point.y)),
  };
}

export function addAnnotation(
  state: EditorState,
  key: DimensionKey,
  start: NormalizedPoint,
  end: NormalizedPoint,
): EditorState {
  const valueCm = key === "custom" ? 0 : state.dimensions[key].valueCm ?? 0;
  const safeStart = clampPoint(start);
  const safeEnd = clampPoint(end);
  const label = {
    x: (safeStart.x + safeEnd.x) / 2,
    y: Math.max(0.05, (safeStart.y + safeEnd.y) / 2 - 0.05),
  };
  const next: DimensionAnnotation = {
    id: crypto.randomUUID(),
    key,
    valueCm,
    start: safeStart,
    end: safeEnd,
    label,
    style: "auto",
  };
  return {
    ...state,
    annotations: [...state.annotations, next],
    selectedAnnotationId: next.id,
  };
}

export function updateAnnotation(
  state: EditorState,
  id: string,
  patch: Partial<DimensionAnnotation>,
): EditorState {
  return {
    ...state,
    annotations: state.annotations.map((item) =>
      item.id === id ? { ...item, ...patch } : item,
    ),
  };
}

export function removeAnnotation(state: EditorState, id: string): EditorState {
  return {
    ...state,
    annotations: state.annotations.filter((item) => item.id !== id),
    selectedAnnotationId: null,
  };
}

export function changeDimensionValue(
  state: EditorState,
  key: Exclude<DimensionKey, "custom">,
  valueCm: number,
): EditorState {
  return {
    ...state,
    dimensions: {
      ...state.dimensions,
      [key]: {
        valueCm,
        provenance: "manual_confirmed",
        evidenceRef: "manual",
      },
    },
    annotations: state.annotations.map((item) =>
      item.key === key ? { ...item, valueCm } : item,
    ),
  };
}

export function nextQueueItem(
  ids: string[],
  currentId: string,
  direction: -1 | 1,
): string {
  if (ids.length === 0) return "";
  const found = ids.indexOf(currentId);
  const index = found < 0 ? 0 : found;
  return ids[Math.min(ids.length - 1, Math.max(0, index + direction))] ?? "";
}

export function canComplete(state: EditorState): { ok: boolean; reason: string } {
  if (!state.selectedAssetId) return { ok: false, reason: "请选择尺寸图素材" };
  if (!state.targetSlotId) return { ok: false, reason: "请选择回写位置" };
  if (state.annotations.length === 0) return { ok: false, reason: "请至少绘制一条尺寸线" };
  const allowed = new Set(["source_confirmed", "manual_confirmed"]);
  const invalid = state.annotations.find(
    (item) =>
      item.key !== "custom" && !allowed.has(state.dimensions[item.key].provenance),
  );
  return invalid
    ? { ok: false, reason: "标注尺寸尚未确认" }
    : { ok: true, reason: "" };
}

export function formatCentimeters(value: number): string {
  return `${Number(value.toFixed(2))} cm`;
}

/** Any semantic edit invalidates the previous deterministic render locally. */
export function invalidateRenderOnEdit(
  item: DimensionCanvasItem,
  editor: EditorState,
): DimensionCanvasItem {
  return {
    ...item,
    editor,
    state: "editing",
    renderRevision: 0,
    renderAssetId: "",
    errorCode: "",
    errorMessage: "",
  };
}
