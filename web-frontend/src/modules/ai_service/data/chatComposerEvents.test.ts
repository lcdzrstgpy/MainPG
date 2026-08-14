import assert from "node:assert/strict";
import test from "node:test";

import { pastedImageFile, shouldSendOnEnter } from "./chatComposerEvents.ts";

test("Enter sends while Shift+Enter and composition keep editing", () => {
  assert.equal(shouldSendOnEnter({ key: "Enter", shiftKey: false, isComposing: false }), true);
  assert.equal(shouldSendOnEnter({ key: "Enter", shiftKey: true, isComposing: false }), false);
  assert.equal(shouldSendOnEnter({ key: "Enter", shiftKey: false, isComposing: true }), false);
  assert.equal(shouldSendOnEnter({ key: "a", shiftKey: false, isComposing: false }), false);
});

test("pasting an image file creates a named image attachment", () => {
  const source = new File(["image"], "", { type: "image/png" });
  const result = pastedImageFile([{ kind: "file", type: "image/png", getAsFile: () => source }], 123);

  assert.equal(result?.type, "image/png");
  assert.equal(result?.name, "pasted-image-123.png");
});

test("pasting text does not create an attachment", () => {
  assert.equal(pastedImageFile([{ kind: "string", type: "text/plain", getAsFile: () => null }]), undefined);
});
