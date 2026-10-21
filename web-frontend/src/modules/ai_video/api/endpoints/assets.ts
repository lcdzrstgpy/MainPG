import { cfJson, type CfInit } from "../clipforgeClient";
import type { Asset, SaveAssetInput } from "../types";

function base(projectId: string) {
  return `/api/project/${encodeURIComponent(projectId)}/assets`;
}

export function listAssets(projectId: string, init?: CfInit) {
  return cfJson<Asset[]>(base(projectId), init);
}

export function saveAsset(projectId: string, input: SaveAssetInput, init?: CfInit) {
  return cfJson<Asset>(base(projectId), { ...init, method: "POST", json: input });
}

/** 同一镜头多 take 只能选中一个；不删除任何媒体。 */
export function selectAsset(projectId: string, assetId: string, init?: CfInit) {
  return cfJson<Asset>(base(projectId), { ...init, method: "PATCH", json: { assetId } });
}
