import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

/**
 * 顶栏几个弹层（主题风格 / 消息中心）的对比度回归。
 *
 * 事故：主题面板容器写死白底 `background: #fff`，但下载版主题包会把
 * `.theme-option` 的文字改成 `var(--theme-text-secondary)` —— diamond 下是
 * `#cbd5e1` 浅灰。实测白底 + `#cbd5e1` = **对比 1.48**，九个主题名几乎看不见。
 *
 * 约定：弹层容器必须跟主题走（`--theme-card-bg-solid`），文字必须用
 * `--theme-text-*` 变量；两边一起变才不会有「白底浅灰字」。
 */
const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

test("主题面板容器跟随主题，不能写死白底", () => {
  const css = read("./styles/global.css");
  const popover = css.slice(css.indexOf(".theme-quick-popover {"), css.indexOf(".theme-quick-header {"));

  assert.match(popover, /background: var\(--theme-card-bg-solid/, "弹层底色必须跟主题");
  assert.doesNotMatch(popover, /background: #fff;/, "不许再写死 #fff");
  assert.match(popover, /border: 1px solid var\(--theme-card-border/);
  // 标题同理：白底改深底后 #1a3551 会看不见
  assert.match(css, /\.theme-quick-header strong \{\n  color: var\(--theme-text-primary/);
});

test("主题选项的文字/边框/选中态都走主题变量", () => {
  const css = read("./styles/themes.css");

  // 基础态
  assert.match(
    css,
    /\.theme-option \{ display: flex;[^}]*color: var\(--theme-text-secondary, #527089\)/,
    "主题名颜色必须跟主题（主题包会给深色主题换浅灰）",
  );
  assert.match(css, /\.theme-option \{ display: flex;[^}]*border: 1px solid var\(--theme-module-border/);
  // hover / 选中
  assert.match(css, /\.theme-option:hover \{[^}]*color: var\(--theme-text-primary/);
  assert.match(css, /\.theme-option\.is-active \{[^}]*background: var\(--theme-module-surface-tint/);
  // 「更多主题」按钮也一样
  assert.match(css.length ? css : "", /\.theme-switcher-label \{[^}]*color: var\(--theme-text-muted/);
});

test("消息中心弹层同样跟随主题，内部文字不许硬编码深色", () => {
  const css = read("./styles/global.css");
  const inbox = css.slice(css.indexOf(".inbox-popover {"), css.indexOf(".inbox-read-all {"));
  assert.match(inbox, /background: var\(--theme-card-bg-solid/);
  assert.doesNotMatch(inbox, /background: #fff;/);

  // 只查消息中心这一段：白底改深底之后，这些原来的固定深色会变成「深底深字」
  const body = css.slice(
    css.indexOf(".inbox-popover {"),
    css.indexOf(".inbox-item-delete:hover"),
  );
  for (const hard of ["color: #1a3551;", "color: #5d7185;", "color: #6f849a;", "color: #9aacbd;"]) {
    assert.ok(!body.includes(hard), `消息中心里还有硬编码文字色 ${hard}`);
  }
  assert.match(body, /\.inbox-item-main strong \{\n  color: var\(--theme-text-primary/);
  assert.match(body, /\.inbox-item-main time \{\n  color: var\(--theme-text-muted/);
});

test("「更多主题」商店弹层跟主题：卡片不能是深色框里的一排白卡", () => {
  const css = read("./styles/global.css");
  const store = css.slice(css.indexOf(".theme-store-back {"), css.indexOf("@media (prefers-reduced-motion: reduce) {\n  .theme-quick-trigger::before"));

  assert.match(store, /\.theme-store-card \{[\s\S]{0,220}background: var\(--theme-module-surface/);
  assert.match(store, /\.theme-store-name \{[\s\S]{0,80}color: var\(--theme-text-primary/);
  assert.match(store, /\.theme-store-desc \{[\s\S]{0,80}color: var\(--theme-text-secondary/);
  // 下载按钮：diamond 的 primary 是近白，白字会消失
  assert.match(store, /\.theme-store-action\.is-download \{[\s\S]{0,120}color: var\(--theme-on-primary/);
  assert.doesNotMatch(store, /\.theme-store-card \{[\s\S]{0,200}background: #fff;/);
});
