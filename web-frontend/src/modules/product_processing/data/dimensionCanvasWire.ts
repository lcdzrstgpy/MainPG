import type {
  DimensionChangeItem,
  DimensionChangeSet,
  DimensionNotification,
  PhysicalDimensions,
  SaveDimensionItemRequest,
} from "../types/dimensionCanvas.ts";

type Json = Record<string, unknown>;

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

export function mapPhysicalDimensions(value: unknown): PhysicalDimensions {
  const raw = record(value);
  const mapValue = (key: "length" | "width" | "height") => {
    const dimension = record(raw[key]);
    const sourceValue = dimension.value_cm ?? dimension.valueCm;
    return {
      valueCm: sourceValue == null || sourceValue === "" ? null : numberValue(sourceValue),
      provenance: stringValue(dimension.provenance || "unconfirmed") as PhysicalDimensions[typeof key]["provenance"],
      evidenceRef: stringValue(dimension.evidence_ref ?? dimension.evidenceRef),
    };
  };
  return {
    length: mapValue("length"),
    width: mapValue("width"),
    height: mapValue("height"),
    conflict: Boolean(raw.conflict),
  };
}

function mapChangeItem(value: unknown): DimensionChangeItem {
  const raw = record(value);
  const baseAsset = record(raw.base_asset);
  const baseSlot = record(baseAsset.slot);
  const replacementAsset = record(raw.replacement_asset);
  return {
    id: stringValue(raw.id ?? raw.change_item_id),
    dimensionItemId: stringValue(raw.dimension_item_id),
    skc: stringValue(raw.skc),
    status: stringValue(raw.status),
    targetSlotId: stringValue(raw.target_slot_id),
    oldImageUrl: stringValue(raw.old_image_url ?? raw.base_asset_url ?? baseSlot.url),
    newImageUrl: stringValue(
      raw.new_image_url
      ?? raw.replacement_asset_url
      ?? replacementAsset.source_url
      ?? replacementAsset.preview_url,
    ),
    physicalDimensions: mapPhysicalDimensions(raw.physical_dimensions),
    conflictReason: stringValue(raw.conflict_reason ?? raw.error_message),
  };
}

export function mapChangeSet(value: unknown): DimensionChangeSet {
  const raw = record(value);
  return {
    id: stringValue(raw.id ?? raw.change_set_id),
    sourceTaskId: numberValue(raw.source_task_id),
    status: stringValue(raw.status),
    itemCount: numberValue(raw.item_count),
    acceptedCount: numberValue(raw.accepted_count),
    conflictCount: numberValue(raw.conflict_count),
    rejectedCount: numberValue(raw.rejected_count),
    items: arrayValue(raw.items).map(mapChangeItem),
    createdAt: stringValue(raw.created_at) || undefined,
  };
}

export function mapDimensionNotifications(payload: unknown): DimensionNotification[] {
  const raw = record(payload);
  const notifications = Array.isArray(raw.notifications)
    ? raw.notifications
    : Array.isArray(payload)
      ? payload
      : [];
  return notifications.map((value) => {
    const notification = record(value);
    const noticePayload = record(notification.payload);
    return {
      id: stringValue(notification.id),
      changeSetId: stringValue(notification.change_set_id ?? noticePayload.change_set_id),
      sourceTaskId: numberValue(notification.source_task_id ?? noticePayload.source_task_id),
      completedCount: numberValue(notification.completed_count ?? notification.item_count ?? noticePayload.completed_count ?? noticePayload.item_count),
      failedCount: numberValue(notification.failed_count ?? noticePayload.failed_count),
      conflictCount: numberValue(notification.conflict_count ?? noticePayload.conflict_count),
      createdAt: stringValue(notification.created_at),
      read: Boolean(notification.read ?? notification.read_at),
    };
  });
}

export function serializeSaveDimensionItemRequest(request: SaveDimensionItemRequest): Json {
  const physicalDimensions = Object.fromEntries(
    (["length", "width", "height"] as const).map((key) => [key, {
      value_cm: request.physical_dimensions[key].valueCm,
      provenance: request.physical_dimensions[key].provenance,
      evidence_ref: request.physical_dimensions[key].evidenceRef,
    }]),
  );
  return {
    expected_revision: request.expected_revision,
    selected_source_asset_id: request.selected_source_asset_id,
    target_slot_id: request.target_slot_id,
    physical_dimensions: {
      ...physicalDimensions,
      conflict: request.physical_dimensions.conflict,
    },
    annotations: request.annotations.map((annotation) => ({
      id: annotation.id,
      key: annotation.key,
      value_cm: annotation.valueCm,
      start: { x: annotation.start.x, y: annotation.start.y },
      end: { x: annotation.end.x, y: annotation.end.y },
      label: { x: annotation.label.x, y: annotation.label.y },
      style: annotation.style,
    })),
    canvas_settings: request.canvas_settings,
  };
}
