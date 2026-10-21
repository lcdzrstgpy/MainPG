import { cfJson, type CfInit } from "../clipforgeClient";

export interface CharacterSheetInput {
  appearance: string;
  name?: string;
  provider: string;
  model: string;
  apiKey: string;
  baseUrl?: string;
  options?: Record<string, unknown>;
}

export interface CharacterSheetResult {
  /** 落盘后的访问路径（/api/files/characters/...） */
  url: string;
  prompt?: string;
}

/** POST /api/characters/sheet —— 一次生成 2×2 四视图定妆照。 */
export function generateCharacterSheet(input: CharacterSheetInput, init?: CfInit) {
  return cfJson<CharacterSheetResult>("/api/characters/sheet", {
    ...init,
    method: "POST",
    json: input,
  });
}
