import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import test from "node:test";

const card = readFileSync(new URL("./CollectionModeCard.tsx", import.meta.url), "utf8");
const page = readFileSync(new URL("../pages/DailySelectionPage.tsx", import.meta.url), "utf8");
/** 采集入口清单已迁到 EntryGate（圆盘选择器）：素材与标签都在这里 */
const gate = readFileSync(new URL("./CollectionEntryGate.tsx", import.meta.url), "utf8");
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
  const srcs = [...gate.matchAll(/src: "(\/assets\/daily-selection\/[^"]+)"/g)].map((m) => m[1]);
  assert.ok(srcs.length >= 8, `背景素材应 ≥8 张（定向 4 + 整店 2 + 插件 2），实际 ${srcs.length}`);
  for (const src of srcs) {
    const file = new URL(`../../../../public${src}`, import.meta.url);
    assert.ok(existsSync(file), `缺少图片文件 ${src}`);
  }
  // 三个入口的标签必须原样保留且可读
  for (const label of ["定向采集", "整店采集", "插件采集"]) {
    assert.ok(gate.includes(`label: "${label}"`), `缺少入口标签 ${label}`);
  }
  // 入口定义必须带轮播素材（三组 SLIDES）
  for (const group of ["SLIDES.daily", "SLIDES.shop", "SLIDES.plugin"]) {
    assert.ok(gate.includes(`slides: ${group}`), `缺少素材组 ${group}`);
  }
});

test("每日选品 hero 跟主题：不许硬编码深色底 + 白字（浅底主题下像贴黑板）", () => {
  const hero = styles.slice(styles.indexOf(".daily-page-heading {"), styles.indexOf(".daily-page-heading-actions"));
  // 底色/边框/文字全部走主题变量：浅色主题是纸感、深色主题自然变深
  assert.match(hero, /background: linear-gradient\(118deg, color-mix\(in srgb, var\(--theme-module-surface-tint/);
  assert.match(hero, /\.daily-page-heading h1 \{[^}]*color: var\(--theme-text-primary/);
  assert.match(hero, /\.daily-page-heading p \{[^}]*color: var\(--theme-text-secondary/);
  assert.doesNotMatch(hero, /#fff;/, "hero 不许写死白色（2026-11-20 用户实测：深绿底贴浅色主题违和）");
  assert.doesNotMatch(hero, /#0a497f 0%, #0d7fba/, "旧的深蓝绿渐变不许回来");
});

test("采集入口卡：旧高特异性规则不得回归（会顶掉卡片内边距，图被挤出白边）", () => {
  // 2026-10-24 实测：shop-collection.css 里的 `.collection-workspace-tabs button`
  // （border-radius:999px + padding:12px 20px）会压掉卡片圆角与内边距，图就被挤出白边。
  assert.doesNotMatch(shopStyles, /\.collection-workspace-tabs button \{/, "旧按钮规则必须保持删除");
  assert.doesNotMatch(shopStyles, /\.collection-tab-thumb \{/, "旧滑块规则必须保持删除");
  // 形状：2026-11 队友把全圆胶囊收成 20px 圆角卡片（全圆时两端各吃掉 60px 弧长，
  // 文字居中显得又扁又空），内边距仍必须为 0 否则图会被挤出白边。
  assert.match(styles, /\.collection-mode-card \{[^}]*border-radius: 20px;/);
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
