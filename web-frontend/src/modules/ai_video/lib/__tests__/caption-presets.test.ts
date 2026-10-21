import assert from "node:assert/strict";
import test from "node:test";

import {
  CAPTION_PRESETS,
  CAPTION_PRESET_IDS,
  captionPresetOverrides,
  isCaptionPreset,
} from "../caption-presets.ts";

test("caption presets validate known ids and reject everything else", () => {
  for (const id of CAPTION_PRESET_IDS) assert.equal(isCaptionPreset(id), true);
  for (const value of ["hormozi", "", undefined, 42]) assert.equal(isCaptionPreset(value), false);
});

test("standard preset mirrors the historical drawtext defaults", () => {
  assert.deepEqual(captionPresetOverrides("standard"), {
    fontSizeRatio: 0.05,
    color: "white",
    strokeWidth: 3,
    box: { color: "black@0.45" },
  });
});

test("bold and minimal disable the background box and bracket the default size", () => {
  assert.equal(captionPresetOverrides("bold").box, false);
  assert.equal(captionPresetOverrides("minimal").box, false);
  assert.ok((captionPresetOverrides("bold").fontSizeRatio ?? 0) > 0.05);
  assert.ok((captionPresetOverrides("bold").strokeWidth ?? 0) > 3);
  assert.ok((captionPresetOverrides("minimal").fontSizeRatio ?? 1) < 0.05);
});

test("karaoke yields no drawtext overrides because the composer switches to ASS", () => {
  assert.equal(CAPTION_PRESETS.karaoke.karaoke, true);
  assert.deepEqual(captionPresetOverrides("karaoke"), {});
});

test("unknown or absent ids yield empty overrides so callers can spread unconditionally", () => {
  assert.deepEqual(captionPresetOverrides(undefined), {});
  assert.deepEqual(captionPresetOverrides("nope"), {});
});
