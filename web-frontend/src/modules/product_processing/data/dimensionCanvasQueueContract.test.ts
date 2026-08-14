import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const queueSource = readFileSync(
  new URL("../components/DimensionCanvasQueue.tsx", import.meta.url),
  "utf8",
);

test("dimension queue owns a cancellable non-passive wheel listener", () => {
  assert.match(
    queueSource,
    /addEventListener\("wheel", handleWheel, \{ passive: false \}\)/,
  );
  assert.match(queueSource, /removeEventListener\("wheel", handleWheel\)/);
  assert.doesNotMatch(queueSource, /<aside[^>]*\sonWheel=/);
  assert.match(queueSource, /dimension_label_outside_safe_margin: "尺寸文字太靠边"/);
});
