import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { buildScriptRequest } from "@/components/project-creation/build-script-request";
import { validateCreationBriefForm } from "@/components/project-creation/creation-brief-defaults";
import { DEFAULT_CREATION_BRIEF } from "@/lib/creation-brief";

const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");

const newProjectPage = read("src/app/project/new/page.tsx");
const form = read("src/components/project-creation/creation-brief-form.tsx");

const FORM_MODULE = "@/components/project-creation/creation-brief-form";

describe("/project/new 改用共享表单（行为等价）", () => {
  it("渲染共享 CreationBriefForm，而不是页内自建表单状态", () => {
    expect(newProjectPage).toMatch(new RegExp(`import \\{[\\s\\S]*?\\bCreationBriefForm\\b[\\s\\S]*?\\} from "${FORM_MODULE}"`));
    expect(newProjectPage).toMatch(/<CreationBriefForm/);
    // 页内不再保留第二套字段状态
    for (const stale of ["setScriptStyle", "setDuration", "setTargetAudience", "setPlatforms", "setPriceRange"]) {
      expect(newProjectPage).not.toMatch(new RegExp(stale));
    }
  });

  it("仍然显示「商品图片」「商品名称」「视频模式」三个可见文案", () => {
    expect(newProjectPage).toMatch(/商品图片/);
    expect(newProjectPage).toMatch(/商品名称/);
    expect(newProjectPage).toMatch(/视频模式/);
  });

  it("脚本请求体只由 buildScriptRequest 构造，没有第二套请求体", () => {
    expect(newProjectPage).toMatch(/from "@\/components\/project-creation\/build-script-request"/);
    // 唯一一处脚本请求，body 就是 buildScriptRequest 的产物
    expect(newProjectPage.match(/\/api\/llm\/script/g)).toHaveLength(1);
    expect(newProjectPage).toMatch(/JSON\.stringify\(\s*buildScriptRequest\(/);
    // 改造前内联拼装的字段行必须消失
    expect(newProjectPage).not.toMatch(/styleType: scriptStyle/);
    expect(newProjectPage).not.toMatch(/targetAudience: targetAudience\.join/);
    expect(newProjectPage).not.toMatch(/usageAdvantage: usageAdvantage/);
    expect(newProjectPage).not.toMatch(/parseInt\(duration\)/);
  });

  it("创建 DTO 保留原有键，并新增统一的 creationBrief", () => {
    expect(newProjectPage).toMatch(/productName/);
    expect(newProjectPage).toMatch(/productCategory: /);
    expect(newProjectPage).toMatch(/productDescription: /);
    expect(newProjectPage).toMatch(/productImages: \[\]/);
    expect(newProjectPage).toMatch(/creationBrief/);
  });

  it("广告/我的/AI 定制模板能力保留，但只作为预填简报的来源", () => {
    expect(newProjectPage).toMatch(/pickAdTemplate/);
    expect(newProjectPage).toMatch(/prefillKey/);
    expect(newProjectPage).toMatch(/adTemplateScriptDirective/);
    expect(newProjectPage).toMatch(/recommendAdTemplates/);
    expect(newProjectPage).toMatch(/generateAiTemplate/);
    expect(newProjectPage).toMatch(/api\/ad-template\/mine/);
    // 模板只预填，不得绕过共享表单直接构造创建请求
    expect(newProjectPage).not.toMatch(/createProject: true/);
  });
});

describe("共享表单的页面接线 props", () => {
  it("新增的接线 props 全部可选，简报回调签名保持不变", () => {
    expect(form).toMatch(/onSubmit: \(brief: CreationBrief\) => void/);
    expect(form).toMatch(/onSubmitForm\?: \(values: CreationBriefFormValues\) => void/);
    expect(form).toMatch(/onValuesChange\?: \(values: CreationBriefFormValues\) => void/);
    expect(form).toMatch(/prefill\?: CreationBriefFormPrefill/);
    expect(form).toMatch(/prefillKey\?: string/);
    expect(form).toMatch(/onImportLink\?: \(url: string\) => void/);
    expect(form).toMatch(/linkImported\?: boolean/);
    expect(form).toMatch(/importError\?: string/);
  });

  it("表单仍然只收集与校验：不发起任何请求", () => {
    expect(form).not.toMatch(/\bfetch\(/);
    expect(form).not.toMatch(/\/api\//);
    expect(form).not.toMatch(/await\s/);
    expect(form).not.toMatch(/recordCreationEvent/);
  });

  it("链接导入后不再强制本地图片，但普通模式仍要求至少一张商品图", () => {
    expect(validateCreationBriefForm({ inputMode: "link", productName: "桂花乌龙茶", images: [] }).valid).toBe(false);
    expect(validateCreationBriefForm({ inputMode: "link", productName: "桂花乌龙茶", images: [], linkImported: true }).valid).toBe(true);
    expect(validateCreationBriefForm({ inputMode: "upload", productName: "桂花乌龙茶", images: [], linkImported: true }).valid).toBe(false);
  });
});

describe("脚本请求仍与改造前逐键等价（黄金对象）", () => {
  it("buildScriptRequest 的键集不受本批接线影响", () => {
    const request = buildScriptRequest({
      brief: { ...DEFAULT_CREATION_BRIEF, styleType: "drama" },
      projectId: "project-1",
      productName: "桂花乌龙茶",
      category: "food",
      productDescription: "0 糖 0 卡",
      productImages: ["/api/files/a.png"],
      videoMode: "product_closeup",
      llmConfig: { baseUrl: "https://llm.example.com/v1", apiKey: "sk-test", model: "doubao-pro" },
    });
    expect(Object.keys(request).sort()).toEqual(
      [
        "projectId", "productName", "category", "productDescription", "targetDuration", "styleType",
        "videoMode", "productImages", "llmConfig", "priceRange", "targetAudience", "platforms",
        "usageAdvantage",
      ].sort()
    );
    expect(request.styleType).toBe("drama");
  });
});
