import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { resolveStrategyGate, strategyGateError, type StrategyChain } from "@/lib/strategy-chain-gate";

/**
 * 三条出片链路各自的服务端策略门禁（与 film-chain-gate 同一口径：正向白名单 + 失败关闭）。
 *
 * 门禁回答的是「这条链属不属于你这个策略」：
 * - free（免费草稿链）：只有 draft；
 * - keyframe（关键帧生图）：controlled-motion 与 native-film 都要关键帧，draft 不要；
 * - motion（逐镜 I2V）：只有 controlled-motion；native-film 走整片，不做逐镜。
 * 旧项目（无简报）在所有链上原样放行。
 */
const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");

const gateModule = read("src/lib/strategy-chain-gate.ts");
const pipelineRoute = read("src/app/api/project/[id]/pipeline/route.ts");
const aiVideoRoute = read("src/app/api/ai/video/route.ts");
const aiImageRoute = read("src/app/api/ai/image/route.ts");
const assetsPage = read("src/app/project/[id]/assets/page.tsx");

const CHAIN_CASES: Array<{ chain: StrategyChain; allow: string[]; deny: string[] }> = [
  { chain: "free", allow: ["draft"], deny: ["controlled-motion", "native-film"] },
  { chain: "keyframe", allow: ["controlled-motion", "native-film"], deny: ["draft"] },
  { chain: "motion", allow: ["controlled-motion"], deny: ["draft", "native-film"] },
];

describe("resolveStrategyGate：每条链的策略白名单", () => {
  for (const { chain, allow, deny } of CHAIN_CASES) {
    it(`${chain}：放行 ${allow.join(" / ")}，拒绝 ${deny.join(" / ")}`, () => {
      for (const strategy of allow) {
        expect(resolveStrategyGate({ chain, projectExists: true, creationBrief: { outputStrategy: strategy } }))
          .toEqual({ allowed: true });
      }
      for (const strategy of deny) {
        expect(resolveStrategyGate({ chain, projectExists: true, creationBrief: { outputStrategy: strategy } }))
          .toEqual({ allowed: false, reason: "strategy-denied", strategy });
      }
    });
  }

  it("项目存在且无简报（null/undefined 的旧项目）在每条链上都放行", () => {
    for (const { chain } of CHAIN_CASES) {
      expect(resolveStrategyGate({ chain, projectExists: true, creationBrief: null })).toEqual({ allowed: true });
      expect(resolveStrategyGate({ chain, projectExists: true, creationBrief: undefined })).toEqual({ allowed: true });
    }
  });

  it("简报损坏（无 outputStrategy）/ 未知未来策略 / 非字符串策略一律拒绝（fail-closed）", () => {
    for (const { chain } of CHAIN_CASES) {
      expect(resolveStrategyGate({ chain, projectExists: true, creationBrief: {} }))
        .toEqual({ allowed: false, reason: "strategy-denied", strategy: "unknown" });
      expect(resolveStrategyGate({ chain, projectExists: true, creationBrief: { outputStrategy: "hologram" } }))
        .toEqual({ allowed: false, reason: "strategy-denied", strategy: "hologram" });
      expect(resolveStrategyGate({ chain, projectExists: true, creationBrief: { outputStrategy: 123 } }))
        .toEqual({ allowed: false, reason: "strategy-denied", strategy: "123" });
    }
  });

  it("项目不存在一律拒绝（即使简报看起来合法）", () => {
    expect(resolveStrategyGate({ chain: "free", projectExists: false, creationBrief: { outputStrategy: "draft" } }))
      .toEqual({ allowed: false, reason: "project-missing" });
  });
});

describe("strategyChainGuard：数据库异常失败关闭", () => {
  it("读库错误直接拒绝，绝不把异常转成字符串再进白名单", () => {
    expect(gateModule).toMatch(/\.catch\(\(\) => null\)/);
    expect(gateModule).toMatch(/if \(rows === null\) return \{ allowed: false, reason: "db-error" \}/);
  });

  it("项目行不存在也拒绝", () => {
    expect(gateModule).toMatch(/resolveStrategyGate\(\{ chain, projectExists: rows\.length > 0, creationBrief: rows\[0\]\?\.brief \}\)/);
  });
});

describe("strategyGateError：拒绝原因映射", () => {
  it("策略拒绝 → 409，数据库错误 → 500，项目不存在 → 404", () => {
    expect(strategyGateError({ allowed: false, reason: "strategy-denied", strategy: "draft" }, "free").status).toBe(409);
    expect(strategyGateError({ allowed: false, reason: "db-error" }, "free").status).toBe(500);
    expect(strategyGateError({ allowed: false, reason: "project-missing" }, "free").status).toBe(404);
  });

  it("文案带上具体策略与链路名，便于用户看懂为什么被拦", () => {
    const denied = strategyGateError({ allowed: false, reason: "strategy-denied", strategy: "native-film" }, "motion");
    expect(denied.zh).toContain("native-film");
    expect(denied.zh).toContain("逐镜动态链");
  });
});

describe("各提交路由的门禁接线", () => {
  it("免费草稿链：/pipeline 导入并调用 free 门禁", () => {
    expect(pipelineRoute).toMatch(/import \{ strategyChainGuard, strategyGateError \} from "@\/lib\/strategy-chain-gate"/);
    expect(pipelineRoute).toMatch(/const gate = await strategyChainGuard\(id, "free"\)/);
    expect(pipelineRoute).toMatch(/apiError\(req, error\.zh, error\.en, error\.status\)/);
  });

  it("逐镜生视频：/api/ai/video 只对带 projectId 的项目内调用做 motion 门禁", () => {
    expect(aiVideoRoute).toMatch(/if \(typeof projectId === "string" && projectId\)/);
    expect(aiVideoRoute).toMatch(/strategyChainGuard\(projectId, "motion"\)/);
  });

  it("关键帧生图：/api/ai/image 对带 projectId 的调用做 keyframe 门禁，且素材页确实带上了 projectId", () => {
    expect(aiImageRoute).toMatch(/strategyChainGuard\(projectId, "keyframe"\)/);
    expect(assetsPage).toMatch(/\/api\/ai\/image[\s\S]{0,800}projectId: id/);
  });
});
