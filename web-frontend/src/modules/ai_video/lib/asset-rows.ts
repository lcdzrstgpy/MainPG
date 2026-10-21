import type { Asset, Shot } from "../api/types";

/** 素材页的镜头视图：一个镜头一行；locked 即用户选定的 take，自动填充会跳过它。 */
export interface ShotAssetRow {
  shot: Shot;
  /** 已锁定的 take（PATCH selected） */
  locked: Asset | null;
  /** 当前展示的 take（未锁定时回落到最新完成项） */
  display: Asset | null;
  /** 该镜头全部 take，锁定项排第一 */
  candidates: Asset[];
  /** 可展示的缩略图（视频无缩略图时为 null） */
  thumbnail: string | null;
  isVideo: boolean;
  status: "missing" | "generating" | "done" | "failed";
}

const VIDEO_FILE = /\.(mp4|webm|mov|m4v)(?:\?|$)/i;

export function isVideoAsset(asset: Asset): boolean {
  return VIDEO_FILE.test(asset.filePath ?? asset.sourceUrl ?? "");
}

export function buildShotAssetRows(shots: readonly Shot[], assets: readonly Asset[]): ShotAssetRow[] {
  const byShot = new Map<number, Asset[]>();
  for (const asset of assets) {
    const list = byShot.get(asset.shotId) ?? [];
    list.push(asset);
    byShot.set(asset.shotId, list);
  }

  return shots.map((shot) => {
    const all = byShot.get(shot.shotId) ?? [];
    const done = all.filter((asset) => asset.status === "done");
    const locked = done.find((asset) => asset.selected) ?? null;
    const display = locked ?? done[0] ?? null;
    const candidates = [...all].sort((a, b) => {
      if (a.selected !== b.selected) return a.selected ? -1 : 1;
      if (a.status !== b.status) return a.status === "done" ? -1 : b.status === "done" ? 1 : 0;
      return String(b.createdAt).localeCompare(String(a.createdAt));
    });
    const active = all.some((asset) => asset.status === "pending" || asset.status === "generating");
    const video = display ? isVideoAsset(display) : false;

    return {
      shot,
      locked,
      display,
      candidates,
      thumbnail: display ? display.thumbnailPath ?? (video ? null : display.filePath) : null,
      isVideo: video,
      status: display ? "done" : active ? "generating" : all.some((asset) => asset.status === "failed") ? "failed" : "missing",
    };
  });
}
