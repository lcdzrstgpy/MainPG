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
    library_asset_ids: [...(manifest.library_asset_ids ?? [])],
    semantic_asset_ids: { ...manifest.semantic_asset_ids },
  };
}

function dedupeIds(ids: string[]): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const id of ids) {
    const value = (id ?? "").trim();
    if (value && !seen.has(value)) {
      seen.add(value);
      result.push(value);
    }
  }
  return result;
}

export function normalizeManifest(manifest: PreviewImageManifest): PreviewImageManifest {
  const carousel = dedupeIds(manifest.carousel_asset_ids);
  const detail = dedupeIds(manifest.detail_asset_ids);
  const library = dedupeIds(manifest.library_asset_ids ?? []);
  let main = (manifest.main_asset_id ?? "").trim();
  if (carousel.length > 0) {
    main = carousel[0];
  } else if (main) {
    carousel.push(main);
  }
  const carouselSet = new Set(carousel);
  const semantic: Record<string, string> = {};
  for (const [slot, id] of Object.entries(manifest.semantic_asset_ids ?? {})) {
    if (id && carouselSet.has(id)) semantic[slot] = id;
  }
  if (carousel.length > 0) semantic["carousel.hero"] = carousel[0];
  return {
    main_asset_id: main,
    carousel_asset_ids: carousel,
    detail_asset_ids: detail,
    library_asset_ids: library,
    semantic_asset_ids: semantic,
  };
}

export function promoteToLibrary(manifest: PreviewImageManifest, assetId: string): PreviewImageManifest {
  const next = copyManifest(manifest);
  next.library_asset_ids = dedupeIds([...next.library_asset_ids, assetId]);
  return normalizeManifest(next);
}

export function removeFromLibrary(manifest: PreviewImageManifest, assetId: string): PreviewImageManifest {
  const next = copyManifest(manifest);
  next.library_asset_ids = next.library_asset_ids.filter((id) => id !== assetId);
  return normalizeManifest(next);
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
    if (usableIds.length === 0) return normalizeManifest(next);
    const mainId = usableIds[0];
    next.carousel_asset_ids = dedupeIds([mainId, ...next.carousel_asset_ids]);
    return normalizeManifest(next);
  }
  if (target === "carousel") {
    next.carousel_asset_ids = appendNewIds(next.carousel_asset_ids, usableIds);
    return normalizeManifest(next);
  }
  next.detail_asset_ids = appendNewIds(next.detail_asset_ids, usableIds);
  return normalizeManifest(next);
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
    if (next.carousel_asset_ids.length === 0) {
      next.main_asset_id = "";
    }
  }
  return {
    manifest: normalizeManifest(next),
    undo: { target, assetId, originalIndex, previousMainAssetId },
  };
}

function insertAtIdentity(assetIds: string[], assetId: string, originalIndex: number): string[] {
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
    next.detail_asset_ids = insertAtIdentity(next.detail_asset_ids, undo.assetId, undo.originalIndex);
  } else {
    next.carousel_asset_ids = insertAtIdentity(next.carousel_asset_ids, undo.assetId, undo.originalIndex);
  }
  return normalizeManifest(next);
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
  if (currentIndex < 0 || destinationIndex < 0 || destinationIndex >= assetIds.length) {
    return normalizeManifest(next);
  }
  [assetIds[currentIndex], assetIds[destinationIndex]] = [
    assetIds[destinationIndex],
    assetIds[currentIndex],
  ];
  return normalizeManifest(next);
}

export function selectMainAsset(
  manifest: PreviewImageManifest,
  assetId: string,
): PreviewImageManifest {
  const next = copyManifest(manifest);
  const remaining = next.carousel_asset_ids.filter((id) => id !== assetId);
  next.carousel_asset_ids = [assetId, ...remaining];
  return normalizeManifest(next);
}
