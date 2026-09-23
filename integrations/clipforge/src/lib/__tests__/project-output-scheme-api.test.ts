// @vitest-environment node
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { join } from "path";
import { NextRequest } from "next/server";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import * as schema from "@/lib/db/schema";
import { buildWorkflowPlanForStrategy } from "@/lib/creation-brief";

const state = vi.hoisted(() => ({ db: null as unknown }));
vi.mock("@/lib/db", () => ({ getDb: () => state.db }));

import { POST } from "@/app/api/project/route";
import { PATCH } from "@/app/api/project/[id]/route";

const sqlite = new Database(":memory:");
const db = drizzle(sqlite, { schema });
beforeAll(() => {
  state.db = db;
  migrate(db, { migrationsFolder: join(process.cwd(), "drizzle") });
});
beforeEach(() => {
  db.delete(schema.projectEvents).run();
  db.delete(schema.projects).run();
});
afterAll(() => sqlite.close());

async function createProject(body: unknown) {
  const response = await POST(new NextRequest("http://localhost/api/project", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
  expect(response.status).toBe(201);
  return response.json();
}

async function updateProject(id: string, body: unknown) {
  const response = await PATCH(new NextRequest(`http://localhost/api/project/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }), { params: Promise.resolve({ id }) });
  expect(response.status).toBe(200);
  return response.json();
}

describe("project output scheme API", () => {
  it("persists a sanitized native-film snapshot", async () => {
    const body = await createProject({ creationBrief: {
      outputStrategy: "controlled-motion",
      audioStrategy: "volcengine-tts",
      outputScheme: { id: "native-film", audioStrategy: "volcengine-tts", unsafe: true },
    } });
    expect(body.creationBrief).toMatchObject({ outputScheme: {
      id: "native-film", audioStrategy: "native-audio",
    } });
    expect(body.creationBrief.outputScheme).not.toHaveProperty("unsafe");
    expect(body.creationBrief).toMatchObject({ outputStrategy: "native-film", audioStrategy: "native-audio" });
    expect(db.select().from(schema.projects).all()[0].creationBrief).toEqual(body.creationBrief);
    expect(db.select().from(schema.projectEvents).all()[0].payload)
      .toMatchObject({ outputStrategy: "native-film", audioStrategy: "native-audio" });
  });

  it("derives a motion workflow for controlled-balanced", async () => {
    const body = await createProject({ creationBrief: { outputScheme: { id: "controlled-balanced" } } });
    expect(body.creationBrief.outputStrategy).toBe("controlled-motion");
    expect(body.productionWorkflow.find((stage: { id: string }) => stage.id === "motion"))
      .toMatchObject({ enabled: true });
    expect(body.productionWorkflow).toEqual(buildWorkflowPlanForStrategy("controlled-motion"));
  });

  it("rebuilds the saved workflow when PATCH changes the scheme", async () => {
    const created = await createProject({ creationBrief: { outputScheme: { id: "draft" } } });
    expect(created.productionWorkflow).toEqual(buildWorkflowPlanForStrategy("draft"));

    const updated = await updateProject(created.id, { creationBrief: {
      outputScheme: { id: "controlled-balanced", outputStrategy: "draft", unsafe: true },
      outputStrategy: "draft",
    } });
    expect(updated.creationBrief.outputScheme).toMatchObject({ id: "controlled-balanced", outputStrategy: "controlled-motion" });
    expect(updated.creationBrief.outputScheme).not.toHaveProperty("unsafe");
    expect(updated.productionWorkflow).toEqual(buildWorkflowPlanForStrategy("controlled-motion"));
    const stored = db.select().from(schema.projects).all()[0];
    expect(stored.creationBrief).toEqual(updated.creationBrief);
    expect(stored.productionWorkflow).toEqual(updated.productionWorkflow);
  });
});
