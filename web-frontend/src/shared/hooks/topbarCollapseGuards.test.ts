import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// 背景（2026-10-22）：桌面工作区（≥801px）把顶栏改成了绝对定位钉在工作区原位，
// 窗口本身不滚动——所有页签共用 .content-card 这一个滚动容器（内容从顶栏下方穿过）。
// 而「顶栏滚动收起」的吸顶判定当时只读 window.scrollY，实测滚 .content-card 400px 后
// window.scrollY 仍然是 0 → is-stuck 永不出现 → 整个开关变成死开关（用户反馈"这个没用了"）。
// 这条守卫锁住"滚动源必须包含共享容器"，别再退回只看 window。
test("顶栏收起判定必须监听共享滚动容器 .content-card", () => {
  const source = readFileSync(
    new URL("../../app/layout/TopNavigation.tsx", import.meta.url),
    "utf8",
  );

  assert.ok(
    source.includes('classList.contains("content-card")'),
    "滚动处理要认出 .content-card 的滚动事件（桌面布局真正的滚动源）",
  );
  assert.ok(
    /querySelector<HTMLElement>\("\.content-card"\)/.test(source),
    "要拿到 .content-card 元素并监听它的 scroll",
  );
  assert.ok(
    source.includes("Math.max(window.scrollY"),
    "窄窗口仍走 window.scrollY，两种滚动源要合并取最大值",
  );
});

test("收起态必须关掉顶栏铺满整卡的毛玻璃层", () => {
  const files = [
    new URL("../styles/global.css", import.meta.url),
    new URL("../styles/apple-workspace.css", import.meta.url),
  ];

  for (const url of files) {
    const css = readFileSync(url, "utf8");
    // 选择器写法两套布局略有差异（classic 多一个 .is-pinned），统一按正则匹配。
    const match = css.match(/\.topbar-card[^{}]*is-stuck[^{}]*>\s*\.topbar-glass\s*\{([^}]*)\}/);
    assert.ok(match, `${url.pathname} 缺少收起态的 .topbar-glass 处理`);
    assert.ok(
      /display:\s*none/.test(match![1]),
      `${url.pathname} 收起态必须把 .topbar-glass 关掉（否则内容上方留一条模糊带）`,
    );
  }
});
