import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// 背景（2026-10-15）：新手引导浮层压在深色遮罩上，底色必须用实心的 --theme-card-bg-solid。
// peach(桃花源)/alpine(高山草原) 的 --theme-card-bg 是 20% 透明的"风景玻璃"（为让风景透出来），
// 浮层若裸用它，面板整片会被"洗掉"——用户反馈"这两个主题下看不见"。
test("引导浮层使用实心卡片底色，且各主题都定义了 --theme-card-bg-solid", () => {
  const guide = readFileSync(new URL("./guide-tour.css", import.meta.url), "utf8");
  const themes = readFileSync(new URL("./themes.css", import.meta.url), "utf8");

  // 引导浮层四处底色（提示卡/板块面板/编辑器/编辑器输入框）必须走实心变量
  const solidUses = guide.match(/background: var\(--theme-card-bg-solid,/g) ?? [];
  assert.equal(solidUses.length, 4, "guide-tour.css 应有 4 处使用 --theme-card-bg-solid");

  // 不许再有裸用半透明变量的底色（会在这两个主题下看不见）
  const bareUses = guide.match(/background: var\(--theme-card-bg,/g) ?? [];
  assert.equal(bareUses.length, 0, "引导浮层禁止裸用 --theme-card-bg（半透明）");

  // :root + 各主题都要有实心底色定义（至少 9 处）
  const defs = themes.match(/--theme-card-bg-solid:/g) ?? [];
  assert.ok(defs.length >= 9, `themes.css 应至少 9 处定义 --theme-card-bg-solid（实际 ${defs.length}）`);
});
