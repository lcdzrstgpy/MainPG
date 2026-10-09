import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * 内嵌（embed=mainpg）亮色主题的源码契约测试。
 *
 * MainPG 把 ClipForge 作为 iframe 模块内嵌时，只提供 MainPG 的亮色主题与唯一外层导航。
 * 亮色的落地方式只有一份：globals.css 里 `.mainpg-embedded` 命名空间下的覆盖。
 * 这里逐条锁住它——选择器必须以 `.mainpg-embedded` 开头（绝不外溢到 standalone 暗色界面），
 * 且每条覆盖都要真实存在，避免以后被误删。
 */
const css = readFileSync(resolve(process.cwd(), "src/app/globals.css"), "utf8");
const startPage = readFileSync(resolve(process.cwd(), "src/app/start/page.tsx"), "utf8");

/** 内嵌覆盖段落的起点：这一行之后的内容全部归内嵌亮色所有。 */
const EMBED_MARKER = "/* MainPG owns the surrounding chrome";

interface CssRule {
  /** 归一化后的选择器组（多选择器用逗号分组时压成一行，便于精确匹配） */
  selector: string;
  /** 规则体（不含大括号） */
  body: string;
}

/** 平铺解析样式表（仓库里没有 @media/@supports 嵌套，选择器一律在 `{` 之前）。 */
function parseRules(text: string): CssRule[] {
  const clean = text.replace(/\/\*[\s\S]*?\*\//g, "");
  const rules: CssRule[] = [];
  const pattern = /(?:^|\})\s*([^{}@]+?)\s*\{([^{}]*?)(?=\})/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(clean)) !== null) {
    rules.push({ selector: match[1].trim().replace(/\s+/g, " "), body: match[2] });
  }
  return rules;
}

const RULES = parseRules(css);

/** 取某个选择器组名下所有同名规则体（同名规则可能有多条，例如 `.mainpg-embedded` 令牌块与滚动条块）。 */
function bodiesFor(selector: string): string[] {
  return RULES.filter((rule) => rule.selector === selector).map((rule) => rule.body);
}

describe("MainPG embedded light theme", () => {
  it("overrides the shared design tokens inside .mainpg-embedded", () => {
    const tokenBodies = bodiesFor(".mainpg-embedded");
    expect(tokenBodies.length).toBeGreaterThan(0);
    const tokens = tokenBodies.find((body) => body.includes("--background: #fff7f8"));
    expect(tokens, "内嵌令牌块必须以 MainPG 亮色底为准").toBeTruthy();
    for (const token of [
      "--background: #fff7f8",
      "--foreground: #49343b",
      "--card:",
      "--border: #f0ccd6",
      "--input:",
      "--muted:",
      "--primary: #d65778",
      "--ring:",
      "--radius: 1rem",
    ]) {
      expect(tokens, `内嵌令牌缺少 ${token}`).toContain(token);
    }
    // Tailwind 工具类（bg-background / text-foreground / border-border …）继承的就是这些变量
    expect(css).toMatch(/\.mainpg-embedded\s*\{[^}]*--background:\s*#fff7f8;/);
    expect(css).toMatch(/\.mainpg-embedded\s*\{[^}]*--primary:\s*#d65778;/);
  });

  it("keeps every embed override scoped to .mainpg-embedded (no bare global selector)", () => {
    const at = css.indexOf(EMBED_MARKER);
    expect(at, "globals.css 里必须保留内嵌覆盖段落的起点注释").toBeGreaterThanOrEqual(0);
    const section = parseRules(css.slice(at));
    expect(section.length).toBeGreaterThan(0);
    for (const rule of section) {
      for (const selector of rule.selector.split(",")) {
        expect(selector.trim(), `内嵌段落里的选择器必须带 .mainpg-embedded 前缀：${selector}`).toMatch(
          /^\.mainpg-embedded(\s|:|\[|$)/,
        );
      }
    }
  });

  it("leaves the standalone dark canvas of /start untouched", () => {
    // 这些属于 standalone 暗色界面：内嵌亮色只能靠 globals.css 覆盖，不能改页面自身的样式表
    expect(startPage).toContain("background:#0B0D12");
    expect(startPage).toContain("--text:#EDEFF4");
    expect(startPage).toContain('className="cf-root"');
    expect(startPage).not.toMatch(/reassureLead/);
    expect(startPage).not.toMatch(/<b>Atlas Cloud<\/b>/);
  });

  it("covers selection, scrollbar and placeholder for the embedded light theme", () => {
    const expectRule = (selector: string, declaration: RegExp) => {
      const bodies = bodiesFor(selector);
      expect(bodies.length, `缺少规则 ${selector}`).toBeGreaterThan(0);
      expect(bodies.some((body) => declaration.test(body)), `${selector} 的声明不对`).toBe(true);
    };
    expectRule(".mainpg-embedded", /scrollbar-color:\s*#e8b8c5 transparent/);
    expectRule(".mainpg-embedded ::selection", /background:\s*rgba\(214, 87, 120, \.22\)/);
    expectRule(".mainpg-embedded ::-webkit-scrollbar", /width:\s*10px/);
    expectRule(".mainpg-embedded ::-webkit-scrollbar-track", /background:\s*transparent/);
    expectRule(".mainpg-embedded ::-webkit-scrollbar-thumb", /background-color:\s*#e8b8c5/);
    expectRule(".mainpg-embedded ::-webkit-scrollbar-thumb:hover", /background-color:\s*#dc7892/);
    expectRule(".mainpg-embedded input::placeholder, .mainpg-embedded textarea::placeholder", /color:\s*#b3929c/);
  });

  it("re-colours the hard-coded purple brand gradient to MainPG pink", () => {
    // 只换 background-image：background 简写会重置 .brand-gradient-text 的 background-clip:text
    const bodies = bodiesFor(".mainpg-embedded .brand-gradient, .mainpg-embedded .brand-gradient-text");
    expect(bodies.length, "缺少 brand-gradient 的粉主色覆盖").toBeGreaterThan(0);
    expect(bodies[0]).toMatch(/background-image:\s*linear-gradient\(105deg, #c7486c, #e8899f\)/);
  });

  it("overrides the start page's residual dark accents", () => {
    const rules: Array<[string, RegExp]> = [
      [".mainpg-embedded .cf-h1 .hl", /text-shadow:\s*0 0 34px rgba\(214, 87, 120, \.3\)/],
      [".mainpg-embedded .cf-card", /box-shadow:\s*0 18px 46px -30px rgba\(158, 72, 101, \.4\)/],
      [".mainpg-embedded .cf-notice", /border-color:\s*#f0ccd6;\s*background:\s*#fff0f3;/],
      [".mainpg-embedded .cf-notice a", /background-image:\s*linear-gradient\(105deg, #c7486c, #e8899f\)/],
      [".mainpg-embedded .cf-err", /color:\s*#b03052/],
      [".mainpg-embedded .cf-spin", /border-top-color:\s*#d65778/],
      [".mainpg-embedded .cf-prog-step.on .ic", /border-color:\s*rgba\(214, 87, 120, \.6\)/],
      [".mainpg-embedded .cf-prog-step.done .ic", /border-color:\s*rgba\(214, 87, 120, \.5\)/],
      [".mainpg-embedded .cf-guide-step b", /background:\s*rgba\(214, 87, 120, \.16\)/],
      [".mainpg-embedded .cf-cat.on", /background:\s*rgba\(214, 87, 120, \.08\)/],
      [".mainpg-embedded .cf-trow .trk.hot", /color:\s*#d65778/],
      [
        ".mainpg-embedded .cf-chip:hover, .mainpg-embedded .cf-trow .tclone:hover",
        /border-color:\s*rgba\(214, 87, 120, \.4\)/,
      ],
      [".mainpg-embedded .cf-daily-btn:hover", /box-shadow:\s*inset 0 0 0 1px rgba\(214, 87, 120, \.45\)/],
      [".mainpg-embedded .cf-daily-input", /background:\s*#fff;\s*border-color:\s*#efc9d3;/],
      [".mainpg-embedded .cf-daily-input:focus", /border-color:\s*#dc7892/],
    ];
    for (const [selector, declaration] of rules) {
      const bodies = bodiesFor(selector);
      expect(bodies.length, `缺少规则 ${selector}`).toBeGreaterThan(0);
      expect(bodies.some((body) => declaration.test(body)), `${selector} 的亮色覆盖被删掉了`).toBe(true);
    }
  });

  it("overrides the hard-coded dark fills of the production profile card on /start", () => {
    // 生产档案卡通体是「白/黑半透明」的深色填充，内嵌时按它自带的 aria-labelledby 钩子做受限覆盖
    const rules: Array<[string, RegExp]> = [
      [".mainpg-embedded [aria-labelledby=\"production-profile-title\"]", /background:\s*rgba\(255, 255, 255, \.88\)/],
      [
        ".mainpg-embedded [aria-labelledby=\"production-profile-title\"] [role=\"radio\"][aria-checked=\"false\"]",
        /background:\s*#fff;/,
      ],
      [
        ".mainpg-embedded [aria-labelledby=\"production-profile-title\"] [role=\"radio\"][aria-checked=\"false\"]:hover",
        /background:\s*#fff4f5/,
      ],
      [".mainpg-embedded [aria-labelledby=\"production-profile-title\"] .bg-white\\/10", /background-color:\s*#f0ccd6/],
      [".mainpg-embedded [aria-labelledby=\"production-profile-title\"] .bg-white\\/6", /background-color:\s*#fff0f3/],
      [".mainpg-embedded [aria-labelledby=\"production-profile-title\"] .bg-black\\/15", /background-color:\s*#fff4f5/],
      [".mainpg-embedded [aria-labelledby=\"production-profile-title\"] .border-white\\/6", /border-color:\s*#f0ccd6/],
      [
        ".mainpg-embedded [aria-labelledby=\"production-profile-title\"] .text-amber-300\\/90",
        /color:\s*#b45309/,
      ],
    ];
    for (const [selector, declaration] of rules) {
      const bodies = bodiesFor(selector);
      expect(bodies.length, `缺少规则 ${selector}`).toBeGreaterThan(0);
      expect(bodies.some((body) => declaration.test(body)), `${selector} 的亮色覆盖被删掉了`).toBe(true);
    }
    // 作用域必须挂在这张卡自带的钩子上，不能是裸的 Tailwind 工具类覆盖
    expect(css).toContain('.mainpg-embedded [aria-labelledby="production-profile-title"]');
  });
});