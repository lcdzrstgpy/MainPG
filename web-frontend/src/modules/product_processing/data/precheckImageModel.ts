import type { PreviewImageManifest } from "../types/index.ts";

export type PrecheckImageTarget = "main" | "carousel" | "detail";

export type RemovedAssetUndo = {
  target: PrecheckImageTarget;
  assetId: string;
  originalIndex: number;
  previousMainAssetId: string;
};

function copyManifest(manifest: PreviewImageManifest): PreviewImageManifest {
  return {
    main_asset_id: manifest.main_asset_id,
    carousel_asset_ids: [...manifest.carousel_asset_ids],
    detail_asset_ids: [...manifest.detail_asset_ids],
    semantic_asset_ids: { ...manifest.semantic_asset_ids },
  };
}

function appendNewIds(existing: string[], assetIds: string[]): string[] {
  const result = [...existing];
  const seen = new Set(existing);
  for (const assetId of assetIds) {
    if (!assetId || seen.has(assetId)) continue;
    seen.add(assetId);
    result.push(assetId);
  }
  return result;
}

export function addAssets(
  manifest: PreviewImageManifest,
  target: PrecheckImageTarget,
  assetIds: string[],
): PreviewImageManifest {
  const next = copyManifest(manifest);
  const usableIds = assetIds.filter(Boolean);
  if (target === "main") {
    if (usableIds.length === 0) return next;
    next.main_asset_id = usableIds[0];
    next.carousel_asset_ids = appendNewIds(next.carousel_asset_ids, usableIds);
    return next;
  }
  if (target === "carousel") {
    next.carousel_asset_ids = appendNewIds(next.carousel_asset_ids, usableIds);
    return next;
  }
  next.detail_asset_ids = appendNewIds(next.detail_asset_ids, usableIds);
  return next;
}

function nextMainAfterRemoval(assetIds: string[], removedIndex: number): string {
  if (assetIds.length === 0) return "";
  return assetIds[removedIndex] ?? assetIds[0];
}

export function removeAsset(
  manifest: PreviewImageManifest,
  target: PrecheckImageTarget,
  assetId: string,
): { manifest: PreviewImageManifest; undo: RemovedAssetUndo } {
  const next = copyManifest(manifest);
  const previousMainAssetId = manifest.main_asset_id;
  let originalIndex = -1;

  if (target === "detail") {
    originalIndex = next.detail_asset_ids.indexOf(assetId);
    if (originalIndex >= 0) next.detail_asset_ids.splice(originalIndex, 1);
  } else {
    originalIndex = next.carousel_asset_ids.indexOf(assetId);
    if (originalIndex >= 0) next.carousel_asset_ids.splice(originalIndex, 1);
    if (next.main_asset_id === assetId) {
      next.main_asset_id = nextMainAfterRemoval(next.carousel_asset_ids, Math.max(0, originalIndex));
    } else if (target === "main") {
      next.main_asset_id = nextMainAfterRemoval(next.carousel_asset_ids, Math.max(0, originalIndex));
    }
  }

  return {
    manifest: next,
    undo: { target, assetId, originalIndex, previousMainAssetId },
  };
}

function insertAtIdentity(
  assetIds: string[],
  assetId: string,
  originalIndex: number,
): string[] {
  if (originalIndex < 0) return [...assetIds];
  if (assetIds.includes(assetId)) return [...assetIds];
  const next = [...assetIds];
  const insertionIndex = Math.min(Math.max(originalIndex, 0), next.length);
  next.splice(insertionIndex, 0, assetId);
  return next;
}

export function restoreRemovedAsset(
  manifest: PreviewImageManifest,
  undo: RemovedAssetUndo,
): PreviewImageManifest {
  const next = copyManifest(manifest);
  if (undo.target === "detail") {
    next.detail_asset_ids = insertAtIdentity(
      next.detail_asset_ids,
      undo.assetId,
      undo.originalIndex,
    );
  } else {
    next.carousel_asset_ids = insertAtIdentity(
      next.carousel_asset_ids,
      undo.assetId,
      undo.originalIndex,
    );
    next.main_asset_id = undo.previousMainAssetId;
  }
  return next;
}

export function moveAsset(
  manifest: PreviewImageManifest,
  target: "carousel" | "detail",
  assetId: string,
  delta: -1 | 1,
): PreviewImageManifest {
  const next = copyManifest(manifest);
  const assetIds = target === "carousel" ? next.carousel_asset_ids : next.detail_asset_ids;
  const currentIndex = assetIds.indexOf(assetId);
  const destinationIndex = currentIndex + delta;
  if (currentIndex < 0 || destinationIndex < 0 || destinationIndex >= assetIds.length) return next;
  [assetIds[currentIndex], assetIds[destinationIndex]] = [
    assetIds[destinationIndex],
    assetIds[currentIndex],
  ];
  return next;
}

export function selectMainAsset(
  manifest: PreviewImageManifest,
  assetId: string,
): PreviewImageManifest {
  const next = copyManifest(manifest);
  next.main_asset_id = assetId;
  next.carousel_asset_ids = appendNewIds(next.carousel_asset_ids, [assetId]);
  return next;
}
