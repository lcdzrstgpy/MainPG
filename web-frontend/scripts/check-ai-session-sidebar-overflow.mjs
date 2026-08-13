import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const stylesheet = new URL("../src/modules/ai_service/styles/aiService.css", import.meta.url);
const css = await readFile(stylesheet, "utf8");

assert.match(
  css,
  /\.ai-history-section\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;/s,
  "the history section must form a vertical flex container",
);
assert.match(
  css,
  /\.ai-history-list\s*\{[^}]*min-height:\s*0;[^}]*overflow-y:\s*auto;/s,
  "the history list must shrink and scroll instead of overlapping the local-storage note",
);

console.log("AI session sidebar overflow regression check passed.");
