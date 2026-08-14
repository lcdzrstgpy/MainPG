import { ppRequest, ppUpload, type ApiContext } from "./client";
import {
  mapChangeSet,
  mapDimensionNotifications,
  mapPhysicalDimensions,
  serializeSaveDimensionItemRequest,
} from "../data/dimensionCanvasWire";
import type {
  CanvasItemState,
  DimensionAnnotation,
  DimensionAsset,
  DimensionCanvasBatch,
  DimensionCanvasItem,
  DimensionChangeItem,
  DimensionChangeSet,
  DimensionEligibilityItem,
  DimensionNotification,
  DimensionTaskEligibility,
  EditorState,
  ImportableDimensionTask,
  PhysicalDimensions,
  SaveDimensionItemRequest,
  UploadedDimensionAsset,
} from "../types/dimensionCanvas";

const API_BASE = "/api/product-processing/dimension-canvas";
const context: ApiContext = { baseUrl: "", token: "", workspaceId: "default" };

type Json = Record<string, unknown>;

const EMPTY_DIMENSIONS: PhysicalDimensions = {
  length: { valueCm: null, provenance: "unconfirmed", evidenceRef: "" },
  width: { valueCm: null, provenance: "unconfirmed", evidenceRef: "" },
  height: { valueCm: null, provenance: "unconfirmed", evidenceRef: "" },
  conflict: false,
};

function record(value: unknown): Json {
  return value && typeof value === "object" ? (value as Json) : {};
}

function numberValue(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function arrayValue(value: unknown): Json[] {
  return Array.isArray(value) ? value.map(record) : [];
}

function mapAnnotation(value: unknown): DimensionAnnotation {
  const raw = record(value);
  const point = (input: unknown) => {
    const rawPoint = record(input);
    return { x: numberValue(rawPoint.x), y: numberValue(rawPoint.y) };
  };
  return {
    id: stringValue(raw.id),
    key: stringValue(raw.key || "custom") as DimensionAnnotation["key"],
    valueCm: numberValue(raw.value_cm ?? raw.valueCm),
    start: point(raw.start),
    end: point(raw.end),
    label: point(raw.label),
    style: stringValue(raw.style || "auto") as DimensionAnnotation["style"],
    lineWidth: stringValue(raw.line_width ?? raw.lineWidth ?? "normal") as DimensionAnnotation["lineWidth"],
    unit: stringValue(raw.unit || "cm") as DimensionAnnotation["unit"],
  };
}

function mapAsset(value: unknown): DimensionAsset {
  const raw = record(value);
  return {
    id: stringValue(raw.id),
    role: stringValue(raw.role),
    previewUrl: stringValue(raw.preview_url ?? raw.source_url ?? raw.managed_url ?? raw.previewUrl),
    width: numberValue(raw.width),
    height: numberValue(raw.height),
    availability: stringValue(raw.availability || "metadata") as DimensionAsset["availability"],
    contentHash: stringValue(raw.content_hash ?? raw.contentHash) || undefined,
  };
}

export function mapItem(value: unknown): DimensionCanvasItem {
  const raw = record(value);
  const rawEditor = record(raw.editor ?? raw.editor_state);
  const rawDimensions = raw.physical_dimensions ?? raw.physical_dimensions_json ?? rawEditor.dimensions;
  const rawSettings = record(raw.canvas_settings ?? rawEditor.canvasSettings ?? rawEditor.canvas_settings);
  const dimensions = rawDimensions ? mapPhysicalDimensions(rawDimensions) : EMPTY_DIMENSIONS;
  const editor: EditorState = {
    selectedAssetId: stringValue(raw.selected_source_asset_id ?? rawEditor.selectedAssetId),
    targetSlotId: stringValue(
      raw.target_slot_id ?? rawEditor.targetSlotId ?? "carousel.dimension_background",
    ),
    dimensions,
    annotations: arrayValue(raw.annotations ?? raw.annotations_json ?? rawEditor.annotations).map(mapAnnotation),
    activeTool: "select",
    selectedAnnotationId: null,
    displayUnit: stringValue(rawSettings.display_unit ?? rawSettings.displayUnit ?? "cm") as EditorState["displayUnit"],
    customValueCm: rawSettings.custom_value_cm == null && rawSettings.customValueCm == null
      ? null
      : numberValue(rawSettings.custom_value_cm ?? rawSettings.customValueCm),
  };
  return {
    id: stringValue(raw.id ?? raw.dimension_item_id),
    batchId: stringValue(raw.batch_id),
    taskId: numberValue(raw.task_id),
    taskItemId: numberValue(raw.task_item_id),
    productDraftId: numberValue(raw.product_draft_id),
    skc: stringValue(raw.skc),
    state: stringValue(raw.state || "pending") as CanvasItemState,
    itemRevision: numberValue(raw.item_revision),
    renderRevision: numberValue(raw.render_revision),
    renderAssetId: stringValue(raw.render_asset_id),
    sourcePreviewRevision: numberValue(raw.source_preview_revision),
    assets: arrayValue(raw.assets).map(mapAsset),
    editor,
    errorCode: stringValue(raw.error_code),
    errorMessage: stringValue(raw.error_message),
  };
}

function mapBatch(value: unknown): DimensionCanvasBatch {
  const raw = record(value);
  return {
    id: stringValue(raw.id ?? raw.batch_id),
    sourceTaskId: numberValue(raw.source_task_id),
    status: stringValue(raw.status),
    totalCount: numberValue(raw.total_count),
    completedCount: numberValue(raw.completed_count),
    failedCount: numberValue(raw.failed_count),
    items: arrayValue(raw.items).map(mapItem),
    createdAt: stringValue(raw.created_at) || undefined,
    updatedAt: stringValue(raw.updated_at) || undefined,
  };
}

function unwrap<T>(payload: unknown, key: string, mapper: (value: unknown) => T): T {
  const raw = record(payload);
  return mapper(raw[key] ?? payload);
}

export async function importPreviewItem(request: {
  task_id: number;
  task_item_id: number;
}): Promise<DimensionCanvasItem> {
  const payload = await ppRequest<unknown>(context, `${API_BASE}/items/import-preview-item`, {
    method: "POST",
    body: request,
  });
  return unwrap(payload, "item", mapItem);
}

export async function getDimensionItem(itemId: string): Promise<DimensionCanvasItem> {
  const payload = await ppRequest<unknown>(context, `${API_BASE}/items/${encodeURIComponent(itemId)}`);
  return unwrap(payload, "item", mapItem);
}

export async function saveDimensionItem(
  itemId: string,
  request: SaveDimensionItemRequest,
): Promise<DimensionCanvasItem> {
  const payload = await ppRequest<unknown>(context, `${API_BASE}/items/${encodeURIComponent(itemId)}`, {
    method: "PATCH",
    body: serializeSaveDimensionItemRequest(request),
  });
  return unwrap(payload, "item", mapItem);
}

export async function uploadDimensionAsset(itemId: string, file: File): Promise<UploadedDimensionAsset> {
  const formData = new FormData();
  formData.append("file", file);
  const payload = await ppUpload<unknown>(
    context,
    `${API_BASE}/items/${encodeURIComponent(itemId)}/assets`,
    formData,
  );
  const raw = record(payload);
  return {
    item: mapItem(raw.item ?? payload),
    assetId: stringValue(raw.asset_id),
  };
}

export async function completeDimensionItem(
  itemId: string,
  expectedRevision: number,
): Promise<DimensionCanvasItem> {
  const payload = await ppRequest<unknown>(
    context,
    `${API_BASE}/items/${encodeURIComponent(itemId)}/complete`,
    { method: "POST", body: { expected_revision: expectedRevision } },
  );
  return unwrap(payload, "item", mapItem);
}

export async function retryDimensionRender(itemId: string, expectedRevision: number): Promise<DimensionCanvasItem> {
  const payload = await ppRequest<unknown>(
    context,
    `${API_BASE}/items/${encodeURIComponent(itemId)}/retry-render`,
    { method: "POST", body: { expected_revision: expectedRevision } },
  );
  return unwrap(payload, "item", mapItem);
}

export async function listDimensionBatches(): Promise<DimensionCanvasBatch[]> {
  const payload = await ppRequest<unknown>(context, `${API_BASE}/batches`);
  const raw = record(payload);
  return (Array.isArray(raw.batches) ? raw.batches : Array.isArray(payload) ? payload : []).map(mapBatch);
}

export async function getDimensionBatch(batchId: string): Promise<DimensionCanvasBatch> {
  const payload = await ppRequest<unknown>(context, `${API_BASE}/batches/${encodeURIComponent(batchId)}`);
  return unwrap(payload, "batch", mapBatch);
}

export async function submitDimensionBatchReview(batchId: string): Promise<DimensionChangeSet> {
  const payload = await ppRequest<unknown>(
    context,
    `${API_BASE}/batches/${encodeURIComponent(batchId)}/submit-review`,
    { method: "POST", body: {} },
  );
  return unwrap(payload, "change_set", mapChangeSet);
}

export async function listImportableDimensionTasks(): Promise<ImportableDimensionTask[]> {
  const payload = await ppRequest<unknown>(context, `${API_BASE}/importable-tasks`);
  const raw = record(payload);
  const tasks = Array.isArray(raw.tasks) ? raw.tasks : Array.isArray(payload) ? payload : [];
  return tasks.map((value) => {
    const task = record(value);
    return {
      taskId: numberValue(task.task_id ?? task.id),
      title: stringValue(task.title),
      itemCount: numberValue(task.item_count ?? task.total_count),
      completedAt: stringValue(task.completed_at ?? task.updated_at),
    };
  });
}

function mapEligibilityItems(value: unknown): DimensionEligibilityItem[] {
  return arrayValue(value).map((item) => ({
    taskItemId: numberValue(item.task_item_id ?? item.id),
    skc: stringValue(item.skc),
    label: stringValue(item.label ?? item.title ?? item.skc),
  }));
}

export async function getDimensionTaskEligibility(
  taskId: number,
): Promise<DimensionTaskEligibility> {
  const payload = await ppRequest<unknown>(
    context,
    `${API_BASE}/tasks/${encodeURIComponent(String(taskId))}/eligibility`,
  );
  const raw = record(payload);
  return {
    ready: mapEligibilityItems(raw.ready),
    needsDimensions: mapEligibilityItems(raw.needs_dimensions),
    existingDimension: mapEligibilityItems(raw.existing_dimension),
    assetFailed: mapEligibilityItems(raw.asset_failed),
  };
}

export async function importDimensionTask(request: {
  task_id: number;
  task_item_ids: number[];
  existing_dimension_actions?: Record<string, "keep" | "remake" | "skip">;
}): Promise<DimensionCanvasBatch> {
  const payload = await ppRequest<unknown>(context, `${API_BASE}/batches/import-task`, {
    method: "POST",
    body: request,
  });
  return unwrap(payload, "batch", mapBatch);
}

export async function getDimensionChangeSet(changeSetId: string): Promise<DimensionChangeSet> {
  const payload = await ppRequest<unknown>(
    context,
    `${API_BASE}/change-sets/${encodeURIComponent(changeSetId)}`,
  );
  return unwrap(payload, "change_set", mapChangeSet);
}

export async function acceptDimensionChangeSet(changeSetId: string): Promise<DimensionChangeSet> {
  const payload = await ppRequest<unknown>(
    context,
    `${API_BASE}/change-sets/${encodeURIComponent(changeSetId)}/accept`,
    { method: "POST", body: {} },
  );
  return unwrap(payload, "change_set", mapChangeSet);
}

export async function acceptDimensionChangeItem(
  changeSetId: string,
  changeItemId: string,
): Promise<DimensionChangeSet> {
  const payload = await ppRequest<unknown>(
    context,
    `${API_BASE}/change-sets/${encodeURIComponent(changeSetId)}/items/${encodeURIComponent(changeItemId)}/accept`,
    { method: "POST", body: {} },
  );
  return unwrap(payload, "change_set", mapChangeSet);
}

export async function rejectDimensionChangeItem(
  changeSetId: string,
  changeItemId: string,
): Promise<DimensionChangeSet> {
  const payload = await ppRequest<unknown>(
    context,
    `${API_BASE}/change-sets/${encodeURIComponent(changeSetId)}/items/${encodeURIComponent(changeItemId)}/reject`,
    { method: "POST", body: {} },
  );
  return unwrap(payload, "change_set", mapChangeSet);
}

export async function listDimensionNotifications(after = "", signal?: AbortSignal): Promise<DimensionNotification[]> {
  const query = after ? `?after=${encodeURIComponent(after)}` : "";
  const payload = await ppRequest<unknown>(context, `${API_BASE}/notifications${query}`, { signal });
  return mapDimensionNotifications(payload);
}

export async function markDimensionNotificationRead(notificationId: string): Promise<void> {
  await ppRequest<unknown>(
    context,
    `${API_BASE}/notifications/${encodeURIComponent(notificationId)}/read`,
    { method: "POST", body: {} },
  );
}
