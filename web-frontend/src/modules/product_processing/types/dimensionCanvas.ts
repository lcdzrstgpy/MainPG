export type DimensionKey = "length" | "width" | "height" | "custom";
export type DimensionUnit = "cm" | "mm" | "in" | "ft" | "both";
export type DimensionLineWidth = "thin" | "normal" | "thick";
export type DimensionEndpointStyle = "arrow" | "bar" | "none";

export type DimensionProvenance =
  | "source_confirmed"
  | "manual_confirmed"
  | "unconfirmed"
  | "package_estimate";

export type CanvasItemState =
  | "pending"
  | "editing"
  | "needs_dimensions"
  | "asset_failed"
  | "rendering"
  | "render_retryable"
  | "completed"
  | "submitted"
  | "conflict"
  | "accepted"
  | "skipped";

export interface NormalizedPoint {
  x: number;
  y: number;
}

export interface DimensionAnnotation {
  id: string;
  key: DimensionKey;
  valueCm: number;
  start: NormalizedPoint;
  end: NormalizedPoint;
  label: NormalizedPoint;
  style: "auto" | "dark" | "light" | "gray_dashed";
  lineWidth: DimensionLineWidth;
  endpointStyle: DimensionEndpointStyle;
  unit: DimensionUnit;
}

export interface DimensionValue {
  valueCm: number | null;
  provenance: DimensionProvenance;
  evidenceRef: string;
}

export interface PhysicalDimensions {
  length: DimensionValue;
  width: DimensionValue;
  height: DimensionValue;
  conflict: boolean;
}

export interface DimensionAsset {
  id: string;
  role: string;
  previewUrl: string;
  width: number;
  height: number;
  availability: "metadata" | "ready" | "local" | "published" | "failed";
  contentHash?: string;
}

export interface EditorState {
  selectedAssetId: string;
  targetSlotId: string;
  dimensions: PhysicalDimensions;
  annotations: DimensionAnnotation[];
  activeTool: DimensionKey | "select";
  selectedAnnotationId: string | null;
  displayUnit: DimensionUnit;
  customValueCm: number | null;
  endpointStyle: DimensionEndpointStyle;
}

export interface DimensionCanvasItem {
  id: string;
  batchId: string;
  taskId: number;
  taskItemId: number;
  productDraftId: number;
  skc: string;
  state: CanvasItemState;
  itemRevision: number;
  renderRevision: number;
  renderAssetId: string;
  sourcePreviewRevision: number;
  assets: DimensionAsset[];
  editor: EditorState;
  errorCode: string;
  errorMessage: string;
}

export interface DimensionCanvasBatch {
  id: string;
  sourceTaskId: number;
  status: string;
  totalCount: number;
  completedCount: number;
  failedCount: number;
  items: DimensionCanvasItem[];
  createdAt?: string;
  updatedAt?: string;
}

export interface DimensionChangeItem {
  id: string;
  dimensionItemId: string;
  skc: string;
  status: string;
  targetSlotId: string;
  oldImageUrl: string;
  newImageUrl: string;
  physicalDimensions: PhysicalDimensions;
  conflictReason: string;
}

export interface DimensionChangeSet {
  id: string;
  sourceTaskId: number;
  status: string;
  itemCount: number;
  acceptedCount: number;
  conflictCount: number;
  rejectedCount: number;
  items: DimensionChangeItem[];
  createdAt?: string;
}

export interface SaveDimensionItemRequest {
  expected_revision: number;
  selected_source_asset_id: string;
  target_slot_id: string;
  physical_dimensions: PhysicalDimensions;
  annotations: DimensionAnnotation[];
  canvas_settings: {
    fit: "contain" | "cover";
    style: "auto" | "dark" | "light";
    display_unit: DimensionUnit;
    custom_value_cm: number | null;
    endpoint_style: DimensionEndpointStyle;
  };
}

export interface UploadedDimensionAsset {
  item: DimensionCanvasItem;
  assetId: string;
}

export interface ImportableDimensionTask {
  taskId: number;
  title: string;
  itemCount: number;
  completedAt: string;
}

export interface DimensionEligibilityItem {
  taskItemId: number;
  skc: string;
  label: string;
}

export interface DimensionTaskEligibility {
  ready: DimensionEligibilityItem[];
  needsDimensions: DimensionEligibilityItem[];
  existingDimension: DimensionEligibilityItem[];
  assetFailed: DimensionEligibilityItem[];
}

export interface DimensionNotification {
  id: string;
  changeSetId: string;
  sourceTaskId: number;
  completedCount: number;
  failedCount: number;
  conflictCount: number;
  createdAt: string;
  read: boolean;
}
