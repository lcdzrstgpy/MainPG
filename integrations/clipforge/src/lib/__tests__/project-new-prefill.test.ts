import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  parseStartPrefill,
  toPrefillParams,
  type CreationEntryId,
} from "@/lib/creation-entry-prefill";
import type { InputMode } from "@/lib/creation-brief";

/**
 * 设计 §4：`/start` 是唯一主创建入口，`/project/new` 在兼容期渲染同一份共享表单，
 * 并消费与 `/start` 同构的预填契约（复用 `parseStartPrefill`，不自造第三套解析）。
 *
 * 仓库没有组件渲染器（无 @testing-library/react），沿用 start-page-prefill /
 * new-project-entry 既有的「纯函数直测 + 源码契约」风格。
 */
const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");

const newProjectPage = read("src/app/project/new/page.tsx");
const productsPage = read("src/app/products/page.tsx");

/** 取页面里 `from` 到 `to` 之间的片段，把源码契约断言限定在真正执行该逻辑的代码里。 */
const sliceOf = (source: string, from: string, to: string): string => {
  const start = source.indexOf(from);
  const end = source.indexOf(to, start);
  expect(start, `源码中找不到 ${from}`).toBeGreaterThan(-1);
  expect(end, `源码中找不到 ${from} … ${to}`).toBeGreaterThan(start);
  return source.slice(start, end);
};

/** 预填 effect 的片段：从解析地址栏开始，到 effect 依赖数组结束。 */
const prefillEffectOf = (source: string) =>
  sliceOf(source, "parseStartPrefill(window.location.search", "}, [libraryProducts, pushPrefill]);");

describe("/project/new 消费与 /start 同构的预填参数", () => {
  it("复用共享的 parseStartPrefill / parseClonePrefill，不再自造第二套解析", () => {
    const importBlock =
      newProjectPage.match(/import \{[\s\S]*?\} from "@\/lib\/creation-entry-prefill";/)?.[0] ?? "";
    expect(importBlock).toContain("parseStartPrefill");
    expect(importBlock).toContain("parseClonePrefill");
    expect(importBlock).toContain("CLONE_PREFILL_STORAGE_KEY");
    // 页面不得再自己 new URLSearchParams(...).get("productId") 拼一套解析
    expect(newProjectPage).not.toMatch(/URLSearchParams/);
  });

  it("?productId= 来源是 product-library，且不再把 inputMode 写死为 upload", () => {
    const prefillEffect = prefillEffectOf(newProjectPage);
    expect(prefillEffect).toMatch(/params\.productId/);
    expect(prefillEffect).toMatch(/inputMode: "product-library"/);
    expect(prefillEffect).not.toMatch(/inputMode: "upload"/);
    // 库内的名称/卖点/图片由页面按 id 读取（抓成 File），纯解析只带 id
    expect(prefillEffect).toMatch(/libraryProducts\.find/);
    expect(prefillEffect).toMatch(/fetchImagesAsFiles\(/);
    expect(prefillEffect).toMatch(/pushPrefill\(/);
    // 只预填一次
    expect(prefillEffect).toMatch(/prefilledRef\.current/);
  });

  it("entry=topic 带主题文本，entry=clone 走同一份 localStorage 交接", () => {
    const prefillEffect = prefillEffectOf(newProjectPage);
    expect(prefillEffect).toMatch(/params\.entry === "topic"/);
    expect(prefillEffect).toMatch(/inputMode: "topic"/);
    expect(prefillEffect).toMatch(/params\.entry === "clone"/);
    expect(prefillEffect).toMatch(/parseClonePrefill\(/);
    expect(prefillEffect).toMatch(/clearClonePrefillStorage\(\)/);
    // 参考结构与来源视频不进表单字段，暂存后在创建/生成时透传
    expect(prefillEffect).toMatch(/cloneRef\.current\s*=/);
  });

  it("复刻的参考结构 / 来源视频沿用本页既有创建与脚本链路透传", () => {
    // 脚本请求体仍只由 buildScriptRequest 构造，并带上复刻节奏骨架
    expect(newProjectPage).toMatch(/referenceStructure: referenceStructure \?\? cloneRef\.current\.referenceStructure/);
    // 创建项目的 body 带上参考视频，与 /start 一致落到 sourceVideoUrl
    const createCall = sliceOf(newProjectPage, 'fetch("/api/project"', "if (!projectRes.ok)");
    expect(createCall).toMatch(/creationBrief: brief/);
    expect(createCall).toMatch(/sourceVideoUrl: cloneRef\.current\.referenceVideoUrl/);
  });

  it("非法/缺失参数不动表单，store 未 hydrate 时 effect 重跑", () => {
    const prefillEffect = prefillEffectOf(newProjectPage);
    // 找不到商品时不落 prefilledRef（effect 会在 store 更新后重跑），保持空表单
    expect(prefillEffect).toMatch(/if \(!product\) return/);
    // 认不出的来源不预填：没有任何裸 pushPrefill 落在这三个分支之外
    expect(prefillEffect.match(/pushPrefill\(/g) ?? []).toHaveLength(3);
  });
});

describe("/products 的「做视频」只指向唯一主入口", () => {
  it("produces a single destination: no 导演模式 → /project/new split", () => {
    // 出品路径不得再出现兼容路由
    expect(productsPage).not.toMatch(/\/project\/new/);
    expect(productsPage).not.toMatch(/uiMode/);
    expect(productsPage).not.toMatch(/path: "\/project\/new"/);

    const makeVideo = sliceOf(
      productsPage,
      "const makeVideoHref",
      "// One-click import of example products"
    );
    expect(makeVideo).toMatch(/toPrefillParams\(\{/);
    expect(makeVideo).toMatch(/kind: "product-library"/);
    expect(makeVideo).toMatch(/productId/);
  });

  it("纯函数：商品库入口的 href 只落在主入口 /start", () => {
    const target = toPrefillParams({ kind: "product-library", productId: "p-1" });
    expect(target.href).toBe("/start?entry=product-library&productId=p-1");
    // 兼容路由只在显式指定 path 时出现，页面已不再使用
    const legacy = toPrefillParams({ kind: "product-library", productId: "p-1", path: "/project/new" });
    expect(legacy.href.startsWith("/project/new?")).toBe(true);
  });
});

describe("复用的纯函数解析（parseStartPrefill）", () => {
  /** entry 与 `CreationBrief.inputMode` 同值（共享契约）；这里显式按 inputMode 读取。 */
  const entryAsInputMode = (search: string): InputMode | null => {
    const entry: CreationEntryId | null = parseStartPrefill(search).entry;
    return entry;
  };

  it("entry=product-library → inputMode = product-library", () => {
    expect(entryAsInputMode("?entry=product-library&productId=p-1")).toBe("product-library");
  });

  it("entry=topic → inputMode = topic", () => {
    expect(entryAsInputMode("?entry=topic&topic=%E5%92%96%E5%95%A1")).toBe("topic");
  });

  it("缺失/非法参数 → null，且不抛错", () => {
    for (const search of ["", "?", "?entry=telepathy", "?productId=", "?topic=x"]) {
      expect(entryAsInputMode(search), search).toBeNull();
    }
  });
});
