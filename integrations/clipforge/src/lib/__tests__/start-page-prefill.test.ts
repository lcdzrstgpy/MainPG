import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { resolveStartPrefill } from "@/app/start/page";
import { toPrefillParams } from "@/lib/creation-entry-prefill";

/**
 * 设计 §4 / §6.2：`/start` 是唯一主创建入口，商品库 / 一句话主题 / 爆款复刻只把「一份预填简报」放在
 * query 里（复刻另带 localStorage 暂存），由主入口消费成表单预填。
 *
 * 仓库没有组件渲染器（无 @testing-library/react），所以这里沿用 script-page-strategy.test.ts 的
 * 「纯函数直测 + 源码契约」风格：预填映射是纯函数，请求透传用源码契约断言。
 */
const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");
const startPage = read("src/app/start/page.tsx");

/** 取 href 的 query 部分：预填契约只往 query 放能进 URL 的字段。 */
const searchOf = (href: string) => href.slice(href.indexOf("?"));

/** 页面里 `from` 到 `to` 之间的片段，把源码契约断言限定在真正执行那段逻辑的代码里。 */
const slice = (from: string, to: string): string => {
  const source = startPage.slice(startPage.indexOf(from), startPage.indexOf(to));
  expect(source, `源码中找不到 ${from} … ${to}`).not.toBe("");
  return source;
};

describe("resolveStartPrefill：URL 预填参数 → 一次表单预填", () => {
  it("entry=topic + topic：来源是 topic，并带回主题文本", () => {
    const search = `?entry=topic&topic=${encodeURIComponent("  在家泡一杯手冲咖啡  ")}`;
    const resolved = resolveStartPrefill(search, null);
    expect(resolved?.inputMode).toBe("topic");
    expect(resolved?.prefill.brief?.inputMode).toBe("topic");
    expect(resolved?.prefill.topic).toBe("在家泡一杯手冲咖啡");
    // 主题来源不带商品图：纯函数也不该自己抓图
    expect(resolved?.prefill.images).toBeUndefined();
  });

  it("productId：来源是 product-library，不再是 upload", () => {
    for (const search of ["?productId=p-1", "?entry=product-library&productId=p-1"]) {
      const resolved = resolveStartPrefill(search, null);
      expect(resolved?.inputMode).toBe("product-library");
      expect(resolved?.prefill.brief?.inputMode).toBe("product-library");
      expect(resolved?.prefill.brief?.inputMode).not.toBe("upload");
      // 库内的名称/卖点/图片由页面按 id 读取，纯函数只带 id
      expect(resolved?.productId).toBe("p-1");
      expect(resolved?.prefill.productName).toBeUndefined();
    }
  });

  it("非法/缺失参数一律返回 null，不抛错", () => {
    const searches = [
      "",
      "?",
      "?entry=telepathy",
      "?entry=topic",
      "?entry=product-library",
      "?productId=",
      "?topic=%E5%92%96%E5%95%A1",
    ];
    for (const search of searches) {
      expect(resolveStartPrefill(search, null), search).toBeNull();
    }
  });

  it("clone：query 文本 + 暂存简报/参考结构/商品图合并成一次预填", () => {
    const target = toPrefillParams({
      kind: "clone",
      productName: "桂花乌龙茶",
      sellingPoints: "0 糖 0 卡",
      styleType: "scenario",
      targetDuration: 37,
      referenceStructure: "3 镜：0-3s / 3-8s / 8-15s",
      referenceVideoUrl: "https://example.com/viral.mp4",
      productImages: ["/api/files/clone-prefill-x/1.png"],
    });
    const resolved = resolveStartPrefill(searchOf(target.href), target.storage?.value ?? null);
    expect(resolved?.inputMode).toBe("clone");
    expect(resolved?.prefill.productName).toBe("桂花乌龙茶");
    expect(resolved?.prefill.sellingPoints).toBe("0 糖 0 卡");
    expect(resolved?.prefill.brief?.inputMode).toBe("clone");
    expect(resolved?.prefill.brief?.styleType).toBe("scenario");
    // 37s 按最近档位归位（只能由 parseClonePrefill 归一化）
    expect(resolved?.prefill.brief?.targetDuration).toBe(30);
    // 商品图先落盘成服务端地址，由页面抓成 File；纯函数不碰网络/DOM
    expect(resolved?.prefill.images).toBeUndefined();
    expect(resolved?.productImages).toEqual(["/api/files/clone-prefill-x/1.png"]);
    // 参考结构与来源视频不进表单，只在创建/生成时透传
    expect(resolved?.referenceStructure).toBe("3 镜：0-3s / 3-8s / 8-15s");
    expect(resolved?.referenceVideoUrl).toBe("https://example.com/viral.mp4");
  });

  it("clone 暂存缺失或损坏时不预填，也不抛错", () => {
    const search = searchOf(
      toPrefillParams({ kind: "clone", productName: "桂花乌龙茶", sellingPoints: "0 糖", styleType: "scenario", targetDuration: 30 }).href
    );
    expect(resolveStartPrefill(search, null)).toBeNull();
    expect(resolveStartPrefill(search, "not-json")).toBeNull();
  });
});

describe("/start 消费预填的源码契约", () => {
  it("导入共享预填契约，并在挂载时解析 URL + 复刻暂存", () => {
    const importBlock = startPage.match(/import \{[^}]*\} from "@\/lib\/creation-entry-prefill";/)?.[0] ?? "";
    expect(importBlock).toContain("parseStartPrefill");
    expect(importBlock).toContain("parseClonePrefill");
    expect(importBlock).toContain("CLONE_PREFILL_STORAGE_KEY");

    const prefillEffect = slice("resolveStartPrefill(window.location.search", "}, [libraryProducts, pushPrefill]);");
    // 三个来源都落到同一个预填通道，且只预填一次
    expect(prefillEffect).toMatch(/prefilledRef\.current/);
    expect(prefillEffect).toMatch(/pushPrefill\(/);
    expect(prefillEffect).toMatch(/\.\.\.resolution\.prefill/);
    expect(prefillEffect).toMatch(/fetchImagesAsFiles\(/);
  });

  it("requestScript 把复刻的参考结构交给唯一脚本请求构造器", () => {
    const body = slice("const requestScript = async (pending: PendingScript)", "const runCreation = async");
    expect(body).toMatch(/buildScriptRequest\(\{/);
    expect(body).toMatch(
      /buildScriptRequest\(\{[\s\S]*?referenceStructure: pending\.referenceStructure[\s\S]*?\}\)/
    );
    // PendingScript 必须能带上它（否则创建后的第一次生成就丢了节奏骨架）
    expect(startPage.match(/interface PendingScript \{[\s\S]*?\n\}/)?.[0] ?? "").toMatch(/referenceStructure\?: string/);
  });

  it("创建项目时把复刻的来源视频交给 sourceVideoUrl", () => {
    const createCall = slice('fetch("/api/project"', "if (!projectRes.ok)");
    expect(createCall).toMatch(/creationBrief: brief/);
    expect(createCall).toMatch(/sourceVideoUrl: cloneRef\.current\.referenceVideoUrl/);
  });

  it("复刻交接只发生一次：暂存用过即清", () => {
    const prefillEffect = slice("if (resolution.inputMode === \"clone\")", "pushPrefill(resolution.prefill);");
    expect(prefillEffect).toMatch(/cloneRef\.current\s*=/);
    expect(prefillEffect).toMatch(/clearClonePrefillStorage\(\)/);
  });

  it("风格重试沿用同一份 pending，参考结构不会在重试时丢失", () => {
    expect(startPage).toMatch(/requestScript\(\{ \.\.\.pending, brief \}\)/);
  });
});
