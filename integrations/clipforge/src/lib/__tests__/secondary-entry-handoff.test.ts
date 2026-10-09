import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { SCRIPT_STYLE_VALUES, resolveScriptStyle } from "@/lib/script-style";

/**
 * 设计 §8 阶段 5：商品库 / 主播库 / 爆款复刻 / 话题成片 / 批量出片只输出预填 CreationBrief 或批量
 * 变量，不再创建独立语义的项目。仓库没有组件渲染器（无 @testing-library/react），入口页的这条
 * 契约沿用 start-entry.test.ts 的源码契约风格。
 *
 * 唯一例外是批量出片：它必须为每一件创建项目，因此仍然调用 `POST /api/project`，但请求体必须带
 * 统一 `creationBrief`（显式 outputStrategy，工作流由服务端按策略推导），脚本请求体必须来自唯一
 * 构造器 `buildScriptRequest`。
 */
const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");
const exists = (file: string) => existsSync(resolve(process.cwd(), file));

const TOPIC_PAGE = "src/app/project/topic/page.tsx";
const CLONE_PAGE = "src/app/project/clone/page.tsx";
const PRODUCTS_PAGE = "src/app/products/page.tsx";
const PRESENTERS_PAGE = "src/app/presenters/page.tsx";
const BATCH_PAGE = "src/app/batch/page.tsx";

const topicPage = read(TOPIC_PAGE);
const clonePage = read(CLONE_PAGE);
const productsPage = read(PRODUCTS_PAGE);
const presentersPage = read(PRESENTERS_PAGE);
const batchPage = read(BATCH_PAGE);

const PREFILL_MODULE = "@/lib/creation-entry-prefill";

/** 真正的请求调用（排除注释里提到路径的情况）。 */
const callsPath = (path: string) => new RegExp(`fetch\\(\\s*["'\`]${path}`);

/** 从源码读出字符串常量；常量名由本测试固定，改名即失败——这是有意的契约。 */
const literalOf = (source: string, name: string): string => {
  const match = source.match(new RegExp(`const ${name} = "([^"]+)"`));
  expect(match, `${name} 必须在源码中定义为字符串常量`).not.toBeNull();
  return match![1];
};

describe("旧深链接仍然可访问（页面不得 404）", () => {
  it("五个入口页文件都还在", () => {
    for (const file of [TOPIC_PAGE, CLONE_PAGE, PRODUCTS_PAGE, PRESENTERS_PAGE, BATCH_PAGE]) {
      expect(exists(file), `${file} 不能因为迁移被删掉`).toBe(true);
    }
  });
});

describe("次级入口不再直接创建项目", () => {
  it("话题成片 / 爆款复刻 / 商品库 / 主播库都不再调用 POST /api/project", () => {
    for (const [name, source] of [
      ["topic", topicPage],
      ["clone", clonePage],
      ["products", productsPage],
      ["presenters", presentersPage],
    ] as const) {
      expect(source, `${name} 不得直接创建项目`).not.toMatch(callsPath("/api/project"));
      expect(source, `${name} 不得自带第二条创建语义`).not.toMatch(/creationBrief\s*:/);
    }
  });

  it("话题成片不再调用 /api/topic/script 形成第二条创建链", () => {
    expect(topicPage).not.toMatch(callsPath("/api/topic/script"));
    expect(topicPage).not.toMatch(callsPath("/api/llm/script"));
  });

  it("爆款复刻不再自建项目、也不再自己发脚本请求", () => {
    expect(clonePage).not.toMatch(/createCloneProject/);
    expect(clonePage).not.toMatch(callsPath("/api/llm/script"));
    expect(clonePage).not.toMatch(/\/api\/project\//);
  });
});

describe("每个入口都只产出预填参数", () => {
  it("话题成片 / 商品库 / 爆款复刻都经共享预填模块产出目标地址", () => {
    for (const [name, source] of [
      ["topic", topicPage],
      ["products", productsPage],
      ["clone", clonePage],
    ] as const) {
      expect(source, `${name} 必须复用预填契约`).toContain(`from "${PREFILL_MODULE}"`);
      expect(source, `${name} 必须真的调用 toPrefillParams`).toMatch(/toPrefillParams\(/);
    }
  });

  it("每个入口的 inputMode 取值与来源一一对应", () => {
    expect(topicPage).toMatch(/kind: "topic"/);
    expect(productsPage).toMatch(/kind: "product-library"/);
    expect(clonePage).toMatch(/kind: "clone"/);
    expect(topicPage).not.toMatch(/kind: "upload"/);
  });

  it("主播库只是资料库：没有创建动作，只作为简报里的 characterId 来源", () => {
    expect(presentersPage).toMatch(/PresenterManager/);
    expect(presentersPage).not.toMatch(/toPrefillParams\(/);
    expect(presentersPage).not.toMatch(/characterId/);
  });
});

describe("各入口原有的核心能力都保留", () => {
  it("爆款复刻仍解析参考视频镜头节奏并展示解析结果", () => {
    expect(clonePage).toMatch(/\/api\/replicate\/analyze/);
    expect(clonePage).toMatch(/refAnalysis/);
    expect(clonePage).toMatch(/storyboards/);
    expect(clonePage).toMatch(/realStructureTitle/);
    expect(clonePage).toMatch(/referenceStructure/);
  });

  it("话题成片仍保留一句话主题输入与灵感提示", () => {
    expect(topicPage).toMatch(/exampleTopic1/);
    expect(topicPage).toMatch(/topic/);
  });

  it("商品库仍保留库管理与批量入口", () => {
    expect(productsPage).toMatch(/useProductLibraryStore/);
    expect(productsPage).toMatch(/addProduct/);
    expect(productsPage).toMatch(/removeProduct/);
    expect(productsPage).toMatch(/href="\/batch"/);
  });

  it("批量出片仍保留并发调度与防同质化变量矩阵", () => {
    expect(batchPage).toMatch(/buildVariationPlan/);
    expect(batchPage).toMatch(/CONCURRENCY/);
    expect(batchPage).toMatch(/\/api\/batch/);
  });
});

describe("批量出片复用统一简报与唯一脚本请求构造器", () => {
  it("创建请求带统一 creationBrief（策略显式），不再自带 ad-hoc 创建字段", () => {
    expect(batchPage).toMatch(/buildBatchItemBrief\(/);
    expect(batchPage).toMatch(/creationBrief: /);
    expect(batchPage).toMatch(/outputStrategy/);
    expect(batchPage).toMatch(/resolveBatchItemStyle\(/);
  });

  it("脚本请求体来自唯一构造器，且不再维护第二套风格映射", () => {
    expect(batchPage).toMatch(/buildScriptRequest\(/);
    expect(batchPage).not.toMatch(/styleTypeMap/);
    expect(batchPage).not.toMatch(/styleType: slot\?\.styleType \?\?/);
  });

  it("出片策略在页面上可见可选，且与单项目语义一致（draft / controlled-motion / native-film）", () => {
    expect(batchPage).toMatch(/BATCH_OUTPUT_STRATEGIES/);
    for (const id of ["draft", "controlled-motion", "native-film"]) {
      expect(batchPage).toMatch(new RegExp(`strategy${id === "draft" ? "Draft" : id === "controlled-motion" ? "ControlledMotion" : "NativeFilm"}`));
    }
    expect(batchPage).toMatch(/batchAutoComposeEnabled\(/);
    expect(batchPage).toMatch(/batchItemTargetPath\(/);
  });

  it("默认风格是白名单里的显式值，不是 auto（409 只留给用户显式选择智能推荐）", () => {
    const fallback = literalOf(batchPage, "DEFAULT_BATCH_STYLE");
    expect(SCRIPT_STYLE_VALUES).toContain(fallback);
    expect(fallback).not.toBe("auto");
    expect(fallback).not.toBe("pain-point");
    expect(resolveScriptStyle({ requestedStyle: fallback })).toMatchObject({
      kind: "resolved",
      styleType: fallback,
    });
    expect(batchPage).toMatch(/errorNeedsExplicitStyle/);
  });
});
