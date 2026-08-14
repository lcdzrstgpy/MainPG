import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const stylesheet = new URL("../src/modules/ai_service/styles/aiService.css", import.meta.url);
const pageSource = new URL("../src/modules/ai_service/pages/AiServicePage.tsx", import.meta.url);
const routerSource = new URL("../../local-runtime/wh_local/modules/ai_service/router.py", import.meta.url);
const css = await readFile(stylesheet, "utf8");
const page = await readFile(pageSource, "utf8");
const router = await readFile(routerSource, "utf8");

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
assert.match(page, /selectedModel\.acceptsImageInput/, "chat image upload must depend on the selected model capability");
assert.match(page, /模型请求失败，请切换模型后重试/, "failed image chat requests must explain how to retry");
assert.match(router, /不要寒暄、自我介绍/, "the built-in chat prompt must prevent boilerplate greetings");
assert.match(router, /先给出结论或可执行答案/, "the built-in chat prompt must lead with the answer");
assert.match(router, /Markdown/, "the built-in chat prompt must require structured markdown output");
assert.match(page, /onContextMenu=\{\(event\) => openConversationMenu\(event, conversation\)\}/, "conversation items must open a right-click menu");
assert.match(page, /重命名/, "the right-click menu must offer rename");
assert.match(page, /置顶/, "the right-click menu must offer pinning");
assert.match(page, /删除会话/, "the right-click menu must offer deletion");
assert.match(page, /recentConversationForMode/, "switching modes must select that mode's latest conversation");
assert.match(page, /handleModeChange/, "mode tabs must load their latest matching conversation");

console.log("AI session sidebar regression check passed.");
