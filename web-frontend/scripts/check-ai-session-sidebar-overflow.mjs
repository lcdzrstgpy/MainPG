import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const stylesheet = new URL("../src/modules/ai_service/styles/aiService.css", import.meta.url);
const pageSource = new URL("../src/modules/ai_service/pages/AiServicePage.tsx", import.meta.url);
const css = await readFile(stylesheet, "utf8");
const page = await readFile(pageSource, "utf8");

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
assert.match(page, /ref=\{conversationFlowRef\}/, "the conversation flow must retain a scroll target");
assert.match(page, /onPaste=\{handleChatPaste\}/, "chat input must accept pasted images");
assert.match(page, /onKeyDown=\{handleChatKeyDown\}/, "chat input must send with Enter");
assert.match(page, /isUploadingImage/, "chat input must block sending while a pasted image uploads");

console.log("AI session sidebar regression check passed.");
