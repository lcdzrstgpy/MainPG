import assert from "node:assert/strict";
import test from "node:test";

import {
  CHARACTER_LIBRARY_STORAGE_KEY,
  readCharactersFrom,
  sanitizeCharacters,
  writeCharactersTo,
  type CharacterStorage,
} from "./characterData.ts";

class MemoryStorage implements CharacterStorage {
  readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

test("空 / 损坏的存储返回空数组", () => {
  assert.deepEqual(readCharactersFrom(null), []);
  assert.deepEqual(readCharactersFrom(new MemoryStorage()), []);
  const broken = new MemoryStorage();
  broken.setItem(CHARACTER_LIBRARY_STORAGE_KEY, "{bad json");
  assert.deepEqual(readCharactersFrom(broken), []);
});

test("坏条目丢弃，默认角色只保留一个", () => {
  const characters = sanitizeCharacters({
    state: {
      characters: [
        { id: "c1", name: "小美", appearance: "黑色长直发", referenceImages: ["/api/files/characters/a.png"], isDefault: true },
        { id: "c2", name: "阿强", referenceImages: [], isDefault: true },
        { id: "c3" },
        { id: "c1", name: "重复" },
      ],
    },
  });

  assert.equal(characters.length, 2);
  assert.equal(characters[0].isDefault, true);
  assert.equal(characters[1].isDefault, false);
  assert.equal(characters[0].referenceImages.length, 1);
});

test("声音配置只保留合法字段", () => {
  const [character] = sanitizeCharacters([{
    id: "c1",
    name: "小美",
    referenceImages: [],
    voiceProfile: { style: "温柔女声", speed: 1.1, emotion: "happy" },
  }]);
  assert.deepEqual(character.voiceProfile, { style: "温柔女声", speed: 1.1, emotion: "happy" });

  const [invalid] = sanitizeCharacters([{
    id: "c2",
    name: "阿强",
    referenceImages: [],
    voiceProfile: { emotion: "angry" },
  }]);
  assert.equal(invalid.voiceProfile, undefined);
});

test("写入后能原样读回", () => {
  const storage = new MemoryStorage();
  writeCharactersTo(storage, [{
    id: "c1",
    name: "小美",
    description: "25 岁女生",
    appearance: "黑色长直发",
    referenceImages: ["/api/files/characters/x.png"],
    voiceProfile: { style: "温柔女声" },
    isDefault: true,
  }]);

  const [character] = readCharactersFrom(storage);
  assert.equal(character.name, "小美");
  assert.equal(character.appearance, "黑色长直发");
  assert.equal(character.isDefault, true);
  assert.equal(character.voiceProfile?.style, "温柔女声");
});
