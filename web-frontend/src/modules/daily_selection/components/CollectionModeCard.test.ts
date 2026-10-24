import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import test from "node:test";

const card = readFileSync(new URL("./CollectionModeCard.tsx", import.meta.url), "utf8");
const page = readFileSync(new URL("../pages/DailySelectionPage.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles/daily-selection.css", import.meta.url), "utf8");
const shopStyles = readFileSync(new URL("../styles/shop-collection.css", import.meta.url), "utf8");

test("采集入口卡：只有选中的卡片才轮播，未选中冻结（不启动定时器）", () => {
  assert.match(card, /if \(!isActive \|\| count < 2\) return;/);
  assert.match(card, /window\.setInterval\(/);
  assert.match(card, /return \(\) => window\.clearInterval\(timer\);/);
  // 系统要求减少动态效果时不自动轮播
  assert.match(card, /prefers-reduced-motion: reduce/);
});

test("采集入口卡：选中清晰白字 / 未选中模糊黑字，文字居中且无底色", () => {
  assert.match(styles, /\.collection-mode-card\.is-active \.collection-mode-img \{ filter: none;/);
  assert.match(styles, /\.collection-mode-img \{[^}]*filter: blur\(5px\)/);
  // 文字：居中 + 无底色
  assert.match(card, /className="collection-mode-label"/);
  assert.match(styles, /\.collection-mode-label \{[^}]*z-index: 2;/);
  assert.match(styles, /\.collection-mode-label \{[^}]*justify-content: center;/);
  assert.doesNotMatch(styles, /\.collection-mode-label \{[^}]*background:/, "标签不允许有底色");
  // 颜色规则：未选中黑字（配白纱），选中白字（配暗角）
  assert.match(styles, /\.collection-mode-label \{[^}]*color: #14251f;/);
  assert.match(styles, /\.collection-mode-card\.is-active \.collection-mode-label \{[^}]*color: #ffffff;/);
});

test("采集入口卡：三个入口各自有背景素材，且文件真实存在", () => {
  const srcs = [...page.matchAll(/src: "(\/assets\/daily-selection\/[^"]+)"/g)].map((m) => m[1]);
  assert.ok(srcs.length >= 8, `背景素材应 ≥8 张（定向 4 + 整店 2 + 插件 2），实际 ${srcs.length}`);
  for (const src of srcs) {
    const file = new URL(`../../../../public${src}`, import.meta.url);
    assert.ok(existsSync(file), `缺少图片文件 ${src}`);
  }
  // 三个入口的标签必须原样保留且可读
  for (const label of ["定向采集", "整店采集", "插件采集"]) {
    assert.ok(page.includes(`label="${label}"`), `缺少入口标签 ${label}`);
  }
  assert.match(page, /<CollectionModeCard/);
});

test("采集入口卡：旧高特异性规则不得回归（会顶掉卡片内边距，图被挤出白边）", () => {
  // 2026-10-24 实测：shop-collection.css 里的 `.collection-workspace-tabs button`
  // （border-radius:999px + padding:12px 20px）会压掉卡片圆角与内边距，图就被挤出白边。
  assert.doesNotMatch(shopStyles, /\.collection-workspace-tabs button \{/, "旧按钮规则必须保持删除");
  assert.doesNotMatch(shopStyles, /\.collection-tab-thumb \{/, "旧滑块规则必须保持删除");
  // 形状是胶囊（用户要求保持原样），内边距必须为 0 否则图会被挤出白边
  assert.match(styles, /\.collection-mode-card \{[^}]*border-radius: 999px;/);
  assert.match(styles, /\.collection-mode-card \{[^}]*padding: 0;/);
});

test("采集入口卡：切换过渡用渐变，禁止扫光（用户 2026-10-24 明确「不要扫光」）", () => {
  assert.doesNotMatch(styles, /collection-mode-sweep/, "扫光特效不得出现");
  assert.doesNotMatch(styles, /\.collection-mode-card\.is-active::after \{[^}]*animation:/, "选中态不得挂扫光动画");
  // 衔接靠过渡而非特效
  assert.match(styles, /\.collection-mode-label \{[^}]*transition: color \.32s ease/);
  assert.match(styles, /\.collection-mode-veil \{[^}]*transition: background \.3s ease/);
  assert.match(styles, /@keyframes collection-mode-panel-in/);
});
