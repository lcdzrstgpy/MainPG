// @vitest-environment node
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { NextRequest } from "next/server";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import * as schema from "@/lib/db/schema";
import { buildVoiceReport, type VoiceReport } from "@/lib/voice-report";

const state = vi.hoisted(() => ({ db: null as unknown }));
vi.mock("@/lib/db", () => ({ getDb: () => state.db }));
import { GET } from "@/app/api/project/[id]/compositions/[compositionId]/timeline/route";

/**
 * 按 compositionId 读取成片逐镜音频报告（timeline sidecar）的读接口契约。
 *
 * 为什么要有这个接口：迁移前 video 页只能读「最新一条」合成记录，导出页选中的历史版本
 * 看不到自己的音频报告（计划「未决事项」第 6 条）。
 *
 * 安全边界（与仓库既有读文件方式一致）：
 * - 只允许读本项目 output 目录下的 `<成片文件名>.timeline.json`；
 * - 项目 id / 成片 id 必须形如 /^[a-zA-Z0-9-]+$/，含 `..`、斜杠、空串一律 400；
 * - 跨项目的成片一律 404，不得顺手返回；
 * - 响应体只含文件名与 /api/output 形式的相对 URL，绝不泄露本机绝对路径。
 */
const dataDir = mkdtempSync(join(tmpdir(), "clipforge-composition-timeline-route-"));
const projectA = "project-a";
const projectB = "project-b";
const outputDirA = join(dataDir, "output", projectA);
const outputDirB = join(dataDir, "output", projectB);

const report: VoiceReport = buildVoiceReport({
  shots: [
    { shotId: 1, source: "volcengine" },
    { shotId: 2, source: "edge", reason: "付费语音合成失败，已回退免费 Edge 音色：503" },
    { shotId: 4, source: "none" },
  ],
  warnings: [{ code: "tts_fallback_free", shotId: 2 }],
});

const sidecarOf = (voiceReport: VoiceReport) =>
  JSON.stringify({ version: 1, total: 9.5, boundaries: [3, 6], voiceReport });

const sqlite = new Database(":memory:");
const db = drizzle(sqlite, { schema });

beforeAll(() => {
  // 与 Electron 打包一致的数据目录注入方式：APP_DATA_DIR 决定 output 根
  process.env.APP_DATA_DIR = dataDir;
  state.db = db;
  migrate(db, { migrationsFolder: join(process.cwd(), "drizzle") });
  db.insert(schema.projects)
    .values([{ id: projectA, name: projectA }, { id: projectB, name: projectB }])
    .run();
  mkdirSync(outputDirA, { recursive: true });
  mkdirSync(outputDirB, { recursive: true });

  // 本项目：有 sidecar 的成片（voiceReport 由生产归约函数生成，保证字段与 voice-report.ts 一致）
  writeFileSync(join(outputDirA, "take-with-report.mp4"), "fixture");
  writeFileSync(join(outputDirA, "take-with-report.mp4.timeline.json"), sidecarOf(report), "utf8");
  // 本项目：成片存在但 sidecar 未生成（老成片 / 未写完）
  writeFileSync(join(outputDirA, "take-without-report.mp4"), "fixture");
  // 其它项目：自己的成片 + sidecar，用来证明不会跨项目读取
  const otherOutput = join(outputDirB, "b-only-take.mp4");
  writeFileSync(otherOutput, "fixture");
  writeFileSync(`${otherOutput}.timeline.json`, sidecarOf(report), "utf8");
  // 项目 output 目录之外的同名 sidecar：不得被读取（即使库里存了绝对路径）
  writeFileSync(join(dataDir, "outside.mp4"), "fixture");
  writeFileSync(
    join(dataDir, "outside.mp4.timeline.json"),
    sidecarOf(buildVoiceReport({ shots: [{ shotId: 99, source: "volcengine" }] })),
    "utf8"
  );

  db.insert(schema.compositions)
    .values([
      { id: "take-with-report", projectId: projectA, status: "done", outputPath: join(outputDirA, "take-with-report.mp4") },
      { id: "take-without-report", projectId: projectA, status: "done", outputPath: join(outputDirA, "take-without-report.mp4") },
      { id: "take-still-composing", projectId: projectA, status: "composing", outputPath: null },
      { id: "outside-take", projectId: projectA, status: "done", outputPath: join(dataDir, "outside.mp4") },
      { id: "b-only-take", projectId: projectB, status: "done", outputPath: join(outputDirB, "b-only-take.mp4") },
    ])
    .run();
});

afterAll(() => {
  sqlite.close();
  rmSync(dataDir, { recursive: true, force: true });
  delete process.env.APP_DATA_DIR;
});

function request(id: string, compositionId: string) {
  return GET(
    new NextRequest(`http://localhost/api/project/${id}/compositions/${compositionId}/timeline`),
    { params: Promise.resolve({ id, compositionId }) }
  );
}

describe("按成片版本读取逐镜音频报告", () => {
  it("返回该成片 sidecar 里的 voiceReport，并给出项目内的 sidecar URL", async () => {
    const response = await request(projectA, "take-with-report");
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body).toMatchObject({
      compositionId: "take-with-report",
      status: "ready",
      fileName: "take-with-report.mp4",
      timelineUrl: `/api/output/${projectA}/take-with-report.mp4.timeline.json`,
    });
    // 字段与 src/lib/voice-report.ts 的 VoiceReport 完全一致（含逐镜降级原因）
    expect(body.timeline.voiceReport).toEqual(report);
    expect(body.timeline.voiceReport).toMatchObject({
      version: 1,
      counts: { volcengine: 1, edge: 1, native: 0, none: 1 },
      hasFailures: true,
      failedShotIds: [2],
    });
    expect(body.timeline.voiceReport.shots).toEqual([
      { shotId: 1, source: "volcengine" },
      { shotId: 2, source: "edge", reason: "付费语音合成失败，已回退免费 Edge 音色：503", warning: "tts_fallback_free" },
      { shotId: 4, source: "none" },
    ]);
  });

  it("其它项目的成片一律 404，不得跨项目读取", async () => {
    // projectB 只能读自己的成片
    const own = await request(projectB, "b-only-take");
    expect(own.status).toBe(200);
    expect((await own.json()).compositionId).toBe("b-only-take");

    const cross = await request(projectA, "b-only-take");
    expect(cross.status).toBe(404);
    const crossBody = await cross.json();
    expect(crossBody).toHaveProperty("error");
    expect(crossBody.timeline).toBeUndefined();
  });

  it("非法项目 id / 成片 id（含 ..、斜杠、空串）返回 400", async () => {
    for (const compositionId of ["", "..", "../take-with-report", "take/with-report", "take with report", "take..report", "take%2Fetc"]) {
      expect((await request(projectA, compositionId)).status).toBe(400);
    }
    for (const id of ["", "..", "../output", "project/a", "project a"]) {
      expect((await request(id, "take-with-report")).status).toBe(400);
    }
  });

  it("成片存在但无 sidecar 与「成片不存在」返回明确区分", async () => {
    const noSidecar = await request(projectA, "take-without-report");
    expect(noSidecar.status).toBe(200);
    const noSidecarBody = await noSidecar.json();
    expect(noSidecarBody).toMatchObject({
      compositionId: "take-without-report",
      status: "missing",
      timeline: null,
      timelineUrl: null,
    });
    expect(noSidecarBody).not.toHaveProperty("error");

    // 还在合成中（没有 outputPath）同样是「未生成」，不是 404
    expect(await (await request(projectA, "take-still-composing")).json()).toMatchObject({
      compositionId: "take-still-composing",
      status: "missing",
      timeline: null,
    });

    const notFound = await request(projectA, "not-a-take");
    expect(notFound.status).toBe(404);
    expect(await notFound.json()).toHaveProperty("error");
  });

  it("只读项目 output 目录下的 sidecar，响应体不含本机绝对路径", async () => {
    // 库里存了 output 目录之外的绝对路径 → 不按该路径读文件，按「未生成」处理
    const outside = await request(projectA, "outside-take");
    expect(outside.status).toBe(200);
    const outsideBody = await outside.json();
    expect(outsideBody).toMatchObject({ status: "missing", timeline: null });

    const bodies = [
      await (await request(projectA, "take-with-report")).json(),
      await (await request(projectA, "take-without-report")).json(),
      outsideBody,
      await (await request(projectA, "not-a-take")).json(),
    ];
    for (const body of bodies) {
      const raw = JSON.stringify(body);
      expect(raw).not.toContain(dataDir);
      expect(raw).not.toContain("outputPath");
      expect(raw).not.toContain('shotId":99');
    }
  });
});
