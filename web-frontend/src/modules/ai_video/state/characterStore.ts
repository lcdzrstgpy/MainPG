import { useEffect, useSyncExternalStore } from "react";

import type { Character } from "../api/types";
import {
  CHARACTER_LIBRARY_STORAGE_KEY,
  readCharactersFrom,
  writeCharactersTo,
  type CharacterStorage,
} from "./characterData";

/** 角色库 store：localStorage + useSyncExternalStore，与商品库/设置同一套路。 */

function browserStorage(): CharacterStorage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

let current: Character[] = readCharactersFrom(browserStorage());
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot() {
  return current;
}

function commit(next: Character[]) {
  current = next;
  writeCharactersTo(browserStorage(), current);
  listeners.forEach((listener) => listener());
}

export const characterActions = {
  addCharacter(character: Character) {
    if (current.some((item) => item.id === character.id)) return;
    // 第一个角色自动成为默认。
    const next = current.length === 0 ? [{ ...character, isDefault: true }, ...current] : [...current, character];
    commit(next);
  },
  updateCharacter(id: string, updates: Partial<Character>) {
    commit(current.map((item) => (item.id === id ? { ...item, ...updates } : item)));
  },
  removeCharacter(id: string) {
    const removed = current.find((item) => item.id === id);
    const next = current.filter((item) => item.id !== id);
    // 删掉默认角色时把默认权交给剩下的第一个。
    if (removed?.isDefault && next.length > 0) next[0] = { ...next[0], isDefault: true };
    commit(next);
  },
  /** 设为默认：其余角色的 isDefault 一并清除。 */
  setDefault(id: string) {
    commit(current.map((item) => ({ ...item, isDefault: item.id === id })));
  },
  getDefault(): Character | undefined {
    return current.find((item) => item.isDefault);
  },
} as const;

export type CharacterActions = typeof characterActions;

export function getCharactersSnapshot(): Character[] {
  return current;
}

export function reloadCharactersFromStorage(): void {
  const next = readCharactersFrom(browserStorage());
  if (JSON.stringify(next) === JSON.stringify(current)) return;
  current = next;
  listeners.forEach((listener) => listener());
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== CHARACTER_LIBRARY_STORAGE_KEY) return;
    reloadCharactersFromStorage();
  });
}

export function useCharacterStore(): { characters: Character[] } & CharacterActions {
  const characters = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  useEffect(() => {
    reloadCharactersFromStorage();
  }, []);
  return { characters, ...characterActions };
}
