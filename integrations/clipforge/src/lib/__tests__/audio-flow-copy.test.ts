import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");

const scriptMsgs = read("src/lib/i18n/messages/script.ts");
const commonMsgs = read("src/lib/i18n/messages/common.ts");
const assetsMsgs = read("src/lib/i18n/messages/assets.ts");
const scriptPage = read("src/app/project/[id]/script/page.tsx");
const detailView = read("src/lib/project-detail-view.ts");
const productionSystem = read("src/lib/production-system.ts");
const pipelineRoute = read("src/app/api/project/[id]/pipeline/route.ts");

describe("免费草稿链路文案：免费 Edge 配音", () => {
  it("autoFinishHint 明确标注免费草稿自动任务 + 免费 Edge 配音", () => {
    expect(scriptMsgs).toMatch(/免费草稿自动任务/);
    expect(scriptMsgs).toMatch(/免费 Edge 配音/);
  });

  it("autoModeHint 用「免费 Edge 配音合成」替代旧「配音合成」", () => {
    expect(scriptMsgs).toMatch(/免费 Edge 配音合成/);
  });
});

describe("转手动 & 界面模式文案", () => {
  it("autoModeManual 明确任务是继续运行，而不是取消", () => {
    expect(scriptMsgs).toMatch(/进入导演模式（任务继续运行）/);
  });

  it("uiModeTip 说明界面模式只决定复杂度，不改数据与出片策略", () => {
    expect(commonMsgs).toMatch(/界面模式只决定界面复杂度/);
    expect(commonMsgs).toMatch(/切换界面模式不会改变项目数据与出片策略/);
  });
});

describe("云端生成任务文案统一", () => {
  it("taskKindPaidUnknown / taskResumeFailed / 诊断文案统一为「云端生成任务」", () => {
    expect(commonMsgs).toMatch(/云端生成任务状态未知，点此核查恢复/);
    expect(assetsMsgs).toMatch(/云端生成任务已失败/);
    expect(productionSystem).toMatch(/云端生成任务可能仍在运行，请继续查询原任务，避免重复扣费/);
  });
});

describe("流水线路由错误文案（仅文案）", () => {
  it("POST / GET 错误串改为免费草稿自动任务", () => {
    expect(pipelineRoute).toMatch(/启动免费草稿自动任务失败/);
    expect(pipelineRoute).toMatch(/查询免费草稿自动任务失败/);
  });
});

describe("音频策略文案", () => {
  it("controlled-motion 说明有可听原生音轨则保留，否则按当前 TTS 设置配音（页面 + 详情视图都出现）", () => {
    expect(scriptPage).toMatch(/有可听原生音轨时保留原音轨，否则按当前 TTS 设置配音。/);
    expect(detailView).toMatch(/有可听原生音轨时保留原音轨，否则按当前 TTS 设置配音。/);
  });

  it("native-film 出现「模型原生音频」", () => {
    expect(scriptPage).toMatch(/模型原生音频/);
  });
});