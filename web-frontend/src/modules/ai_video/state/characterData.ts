/**
 * 主播角色本地数据层（T1.4）：纯函数，可脱离 React 单测。
 *
 * 存储键沿用 ClipForge 的 `daihuo-jianshou-characters`，落在 MainPG origin；
 * 结构兼容 zustand persist 的 `{state:{characters},version}`。
 */

import type { Character, CharacterVoiceProfile } from "../api/types";

export const CHARACTER_LIBRARY_STORAGE_KEY = "daihuo-jianshou-characters";

export interface CharacterStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const VOICE_EMOTIONS: readonly NonNullable<CharacterVoiceProfile["emotion"]>[] = [
  "neutral",
  "happy",
  "serious",
  "energetic",
];

function objectOf(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function sanitizeVoiceProfile(raw: unknown): CharacterVoiceProfile | undefined {
  const profile = objectOf(raw);
  if (!profile) return undefined;
  const style = text(profile.style).trim();
  if (!style && typeof profile.speed !== "number") return undefined;
  return {
    style,
    ...(typeof profile.speed === "number" && Number.isFinite(profile.speed) ? { speed: profile.speed } : {}),
    ...(VOICE_EMOTIONS.includes(profile.emotion as (typeof VOICE_EMOTIONS)[number])
      ? { emotion: profile.emotion as CharacterVoiceProfile["emotion"] }
      : {}),
  };
}

function sanitizeCharacter(raw: unknown): Character | null {
  const item = objectOf(raw);
  if (!item) return null;
  const id = text(item.id);
  const name = text(item.name).trim();
  if (!id || !name) return null;
  const voiceProfile = sanitizeVoiceProfile(item.voiceProfile);
  return {
    id,
    name,
    referenceImages: Array.isArray(item.referenceImages)
      ? item.referenceImages.filter((image): image is string => typeof image === "string" && image.length > 0)
      : [],
    isDefault: item.isDefault === true,
    ...(text(item.description).trim() ? { description: text(item.description).trim() } : {}),
    ...(text(item.appearance).trim() ? { appearance: text(item.appearance).trim() } : {}),
    ...(voiceProfile ? { voiceProfile } : {}),
  };
}

export function sanitizeCharacters(raw: unknown): Character[] {
  let list: unknown[] = [];
  if (Array.isArray(raw)) {
    list = raw;
  } else {
    const state = objectOf(objectOf(raw)?.state);
    if (state && Array.isArray(state.characters)) list = state.characters;
  }
  const seen = new Set<string>();
  const characters: Character[] = [];
  for (const entry of list) {
    const character = sanitizeCharacter(entry);
    if (!character || seen.has(character.id)) continue;
    seen.add(character.id);
    characters.push(character);
  }
  // 默认角色唯一：只保留第一个 isDefault，避免旧数据里多个默认。
  let defaultSeen = false;
  return characters.map((character) => {
    if (!character.isDefault) return character;
    if (defaultSeen) return { ...character, isDefault: false };
    defaultSeen = true;
    return character;
  });
}

export function readCharactersFrom(storage: CharacterStorage | null): Character[] {
  if (!storage) return [];
  try {
    const stored = storage.getItem(CHARACTER_LIBRARY_STORAGE_KEY);
    return stored ? sanitizeCharacters(JSON.parse(stored)) : [];
  } catch {
    return [];
  }
}

export function writeCharactersTo(storage: CharacterStorage | null, characters: Character[]): void {
  if (!storage) return;
  try {
    storage.setItem(
      CHARACTER_LIBRARY_STORAGE_KEY,
      JSON.stringify({ state: { characters }, version: 0 }),
    );
  } catch {
    // 隐私模式 / 配额写满：本次会话内仍可用。
  }
}
