// @vitest-environment node
import { mkdtempSync, writeFileSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { NextRequest } from "next/server";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import * as schema from "@/lib/db/schema";

const state = vi.hoisted(() => ({ db: null as unknown, render: vi.fn().mockResolvedValue(null), preview: vi.fn().mockResolvedValue("data:image/jpeg;base64,test") }));
vi.mock("@/lib/db", () => ({ getDb: () => state.db }));
vi.mock("@/lib/platform-export", () => ({ renderPlatformExport: state.render, previewPlatformExport: state.preview }));
vi.mock("@/lib/export-guard", () => ({ probeEncodeStats: async () => ({ durationSec: 3 }) }));
import { POST } from "@/app/api/project/[id]/export-platform/route";

const directory = mkdtempSync(join(tmpdir(), "clipforge-platform-route-"));
const sqlite = new Database(":memory:");
const db = drizzle(sqlite, { schema });
beforeAll(() => {
  state.db = db;
  migrate(db, { migrationsFolder: join(process.cwd(), "drizzle") });
  for (const id of ["project-a", "project-b"]) db.insert(schema.projects).values({ id, name: id }).run();
  for (const [id, projectId, status, date] of [
    ["old-take", "project-a", "done", 100], ["new-take", "project-a", "done", 200],
    ["failed-take", "project-a", "failed", 300], ["other-take", "project-b", "done", 400],
  ] as const) {
    const outputPath = join(directory, `${id}.mp4`);
    writeFileSync(outputPath, "fixture");
    db.insert(schema.compositions).values({ id, projectId, status, outputPath, createdAt: new Date(date * 1000) }).run();
  }
});
afterAll(() => { sqlite.close(); rmSync(directory, { recursive: true, force: true }); });

async function request(body: unknown) {
  return POST(new NextRequest("http://localhost/api/project/project-a/export-platform", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }), { params: Promise.resolve({ id: "project-a" }) });
}

describe("平台导出版本与输入边界", () => {
  it("显式旧版本不会被新成片替换，缺省跳过失败版本", async () => {
    const pinned = await request({ platform: "douyin", compositionId: "old-take" });
    expect(pinned.status).toBe(200);
    expect(await pinned.json()).toMatchObject({ compositionId: "old-take", framing: { mode: "blur" } });
    expect(state.render.mock.lastCall?.[0].sourcePath).toBe(join(directory, "old-take.mp4"));
    expect(await (await request({ platform: "douyin" })).json()).toMatchObject({ compositionId: "new-take" });
  });

  it("非法、跨项目或失败版本不得退回 latest", async () => {
    for (const compositionId of ["", "../old-take", 12, "other-take", "failed-take", "missing"]) {
      state.render.mockClear();
      expect((await request({ platform: "douyin", compositionId })).status).toBe(400);
      expect(state.render).not.toHaveBeenCalled();
    }
  });

  it("预览锁定版本和裁切参数，越界时间收敛到片尾附近且不导出整片", async () => {
    state.render.mockClear();
    const framing = { mode: "crop", positionX: 1, positionY: 0 };
    const response = await request({ platform: "xiaohongshu", compositionId: "old-take", framing, preview: true, previewTime: 999 });
    expect(await response.json()).toMatchObject({ compositionId: "old-take", framing, previewTime: 2.9, size: "1080x1440" });
    expect(state.preview.mock.lastCall?.[0]).toMatchObject({ time: 2.9, framing, sourcePath: join(directory, "old-take.mp4") });
    expect(state.render).not.toHaveBeenCalled();
  });

  it("拒绝原型键、非法构图和预览参数", async () => {
    for (const body of [
      { platform: "constructor" }, { platform: "__proto__" }, { framing: { mode: "invalid" } },
      { framing: { positionX: 2 } }, { preview: "true" }, { previewTime: -1 }, { previewTime: "2" },
    ]) expect((await request({ platform: "douyin", ...body })).status).toBe(400);
  });
});
