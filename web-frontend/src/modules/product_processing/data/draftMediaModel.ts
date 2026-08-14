import type { DraftMediaGroups, MediaAssetStatus, MediaBindingView } from "../types";

export const DRAFT_MEDIA_GROUPS = [
  "main",
  "gallery",
  "detail",
  "sku",
  "carousel",
  "dimension",
] as const;

export type DraftMediaGroup = (typeof DRAFT_MEDIA_GROUPS)[number];

export function flattenDraftMediaGroups(
  groups: DraftMediaGroups & Record<string, MediaBindingView[]>,
): Array<{ group: string; media: MediaBindingView }> {
  const known = new Set<string>(DRAFT_MEDIA_GROUPS);
  const ordered = [
    ...DRAFT_MEDIA_GROUPS,
    ...Object.keys(groups).filter((group) => !known.has(group)).sort(),
  ];
  return ordered.flatMap((group) => (groups[group] ?? []).map((media) => ({ group, media })));
}

export function supportsMediaRetry(status: MediaAssetStatus): boolean {
  return status === "retryable" || status === "failed";
}

export function mediaStatusLabel(status: MediaAssetStatus): string {
  return ({
    pending: "等待同步",
    materializing: "同步中",
    ready: "可用",
    retryable: "可重试",
    failed: "同步失败",
  })[status];
}
