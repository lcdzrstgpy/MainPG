import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { resolveFilmChainGate, filmChainGateError } from "@/lib/film-chain-gate";

/**
 * 整片链（九宫格 → 原生整片）的服务端出片策略门禁。
 * 正向白名单 + 失败关闭：只有明确的 native-film 或「项目存在且无简报」的旧项目放行；
 * 九宫格路由（controlled-motion 的逐镜 I2V 一致性锚点）必须保持无门禁。
 */
const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");

const gridRoute = read("src/app/api/project/[id]/storyboard-grid/route.ts");
const filmRoute = read("src/app/api/project/[id]/storyboard-film/route.ts");
const gateModule = read("src/lib/film-chain-gate.ts");

describe("resolveFilmChainGate：正向白名单", () => {
  it("明确的 native-film 放行", () => {
    expect(
      resolveFilmChainGate({ projectExists: true, creationBrief: { outputStrategy: "native-film" } })
    ).toEqual({ allowed: true });
  });

  it("项目存在且无简报（null/undefined 的旧项目）放行", () => {
    expect(resolveFilmChainGate({ projectExists: true, creationBrief: null })).toEqual({ allowed: true });
    expect(resolveFilmChainGate({ projectExists: true, creationBrief: undefined })).toEqual({ allowed: true });
  });

  it("draft / controlled-motion 拒绝（无人能绕过按钮直接提交）", () => {
    expect(resolveFilmChainGate({ projectExists: true, creationBrief: { outputStrategy: "draft" } })).toEqual({
      allowed: false,
      reason: "strategy-denied",
      strategy: "draft",
    });
    expect(
      resolveFilmChainGate({ projectExists: true, creationBrief: { outputStrategy: "controlled-motion" } })
    ).toEqual({ allowed: false, reason: "strategy-denied", strategy: "controlled-motion" });
  });

  it("简报损坏（无 outputStrategy）/ 未知未来策略 / 非字符串策略一律拒绝（fail-closed）", () => {
    expect(resolveFilmChainGate({ projectExists: true, creationBrief: {} })).toEqual({
      allowed: false,
      reason: "strategy-denied",
      strategy: "unknown",
    });
    expect(resolveFilmChainGate({ projectExists: true, creationBrief: { outputStrategy: "hologram" } })).toEqual({
      allowed: false,
      reason: "strategy-denied",
      strategy: "hologram",
    });
    expect(resolveFilmChainGate({ projectExists: true, creationBrief: { outputStrategy: 123 } })).toEqual({
      allowed: false,
      reason: "strategy-denied",
      strategy: "123",
    });
  });

  it("项目不存在一律拒绝（即使简报看起来合法）", () => {
    expect(
      resolveFilmChainGate({ projectExists: false, creationBrief: { outputStrategy: "native-film" } })
    ).toEqual({ allowed: false, reason: "project-missing" });
  });
});

describe("filmChainStrategyGuard：数据库异常失败关闭", () => {
  it("读库错误直接拒绝，绝不把异常转成字符串再进白名单", () => {
    expect(gateModule).toMatch(/\.catch\(\(\) => null\)/);
    expect(gateModule).toMatch(/if \(rows === null\) return \{ allowed: false, reason: "db-error" \}/);
  });

  it("项目行不存在也拒绝", () => {
    expect(gateModule).toMatch(/resolveFilmChainGate\(\{ projectExists: rows\.length > 0, creationBrief: rows\[0\]\?\.brief \}\)/);
  });
});

describe("filmChainGateError：拒绝原因映射", () => {
  it("策略拒绝 → 409，数据库错误 → 500，项目不存在 → 404", () => {
    expect(filmChainGateError({ allowed: false, reason: "strategy-denied", strategy: "draft" }).status).toBe(409);
    expect(filmChainGateError({ allowed: false, reason: "db-error" }).status).toBe(500);
    expect(filmChainGateError({ allowed: false, reason: "project-missing" }).status).toBe(404);
  });
});

describe("提交路由的门禁接线", () => {
  it("storyboard-grid 路由保持无门禁（controlled-motion 逐镜 I2V 的九宫格锚点照常可用）", () => {
    expect(gridRoute).not.toMatch(/film-chain-gate/);
    expect(gridRoute).not.toMatch(/filmChainStrategyGuard/);
  });

  it("storyboard-film 路由导入门禁并在 dryRun 之前拦截", () => {
    expect(filmRoute).toMatch(/import \{ filmChainStrategyGuard, filmChainGateError \} from "@\/lib\/film-chain-gate"/);
    expect(filmRoute).toMatch(/const gate = await filmChainStrategyGuard\(id\)/);
    expect(filmRoute).toMatch(/apiError\(req, error\.zh, error\.en, error\.status\)/);
    expect(filmRoute.indexOf("filmChainStrategyGuard(id)")).toBeLessThan(filmRoute.indexOf("if (dryRun)"));
  });
});