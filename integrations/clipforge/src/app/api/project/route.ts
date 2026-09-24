import { NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { projects } from "@/lib/db/schema";
import { desc } from "drizzle-orm";
import { assertStrategyWorkflowConsistency, buildWorkflowPlanForStrategy, sanitizeCreationBrief } from "@/lib/creation-brief";
import { recordCreationEvent } from "@/lib/creation-analytics";
import { sanitizeCreativeIntent, sanitizeVisualBible, sanitizeWorkflowPlan, type WorkflowStagePlan } from "@/lib/production-system";

// fetch project list, most recently edited first (the /start "continue" cards rely on this order)
export async function GET() {
  try {
    const db = getDb();
    const result = await db.select().from(projects).orderBy(desc(projects.updatedAt));
    return NextResponse.json(result);
  } catch (error) {
    console.error("获取项目列表失败:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "获取项目列表失败" },
      { status: 500 }
    );
  }
}

// create a new project
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const db = getDb();

    // validate videoMode / sourceType against enum allowlists; fall back to default for invalid values
    const VIDEO_MODES = ["product_closeup", "graphic_montage", "scene_demo", "live_presenter"];
    const videoMode = VIDEO_MODES.includes(body.videoMode) ? body.videoMode : undefined;
    const sourceType = body.sourceType === "clone" ? "clone" : undefined;

    // Content type + character binding (same allowlist style as videoMode/sourceType): an unknown
    // contentType is ignored so the column default ("product") keeps its legacy meaning, and `topic`
    // is only persisted for topic projects — a product project never stores a stray topic string.
    const CONTENT_TYPES = ["product", "topic"] as const;
    const contentType: (typeof CONTENT_TYPES)[number] | undefined =
      CONTENT_TYPES.includes(body.contentType) ? body.contentType : undefined;
    const characterId = typeof body.characterId === "string" ? body.characterId.trim() || undefined : undefined;
    const topic = typeof body.topic === "string" ? body.topic.trim() || undefined : undefined;

    // Unified creation contract. The brief is always normalized for validation/telemetry, but the
    // column is only written when the caller actually sent one — legacy callers (and projects
    // created before this contract) keep a null brief and their old behaviour.
    const creationBrief = sanitizeCreationBrief(body.creationBrief);
    const hasCreationBrief = body.creationBrief !== undefined;

    // creativeIntent / visualBible are sanitized here so a raw value can never reach the script /
    // assets / compose stages; callers that do not send them still get null (legacy behaviour).
    const creativeIntent = body.creativeIntent !== undefined ? sanitizeCreativeIntent(body.creativeIntent) : undefined;
    const visualBible = body.visualBible !== undefined ? sanitizeVisualBible(body.visualBible) : undefined;

    // A brief-backed project always saves the workflow derived from its sanitized scheme, even if
    // the request also contains a workflow. Legacy callers without a brief retain their existing
    // optional workflow handling and otherwise keep a null workflow.
    let productionWorkflow: WorkflowStagePlan[] | undefined;
    if (hasCreationBrief) {
      productionWorkflow = buildWorkflowPlanForStrategy(creationBrief.outputStrategy);
    } else if (body.productionWorkflow !== undefined) {
      const workflow = sanitizeWorkflowPlan(body.productionWorkflow);
      if (workflow) {
        const conflict = assertStrategyWorkflowConsistency(creationBrief, workflow);
        if (conflict) return NextResponse.json({ error: conflict }, { status: 400 });
        productionWorkflow = workflow;
      }
    }

    const newProject = await db
      .insert(projects)
      .values({
        name: body.name || "未命名项目",
        productName: body.productName,
        productCategory: body.productCategory,
        productDescription: body.productDescription,
        productImages: body.productImages || [],
        ...(videoMode && { videoMode }),
        ...(sourceType && { sourceType }),
        ...(characterId && { characterId }),
        ...(contentType && { contentType }),
        ...(contentType === "topic" && topic && { topic }),
        ...(body.sourceVideoUrl && { sourceVideoUrl: body.sourceVideoUrl }),
        ...(hasCreationBrief && { creationBrief }),
        ...(creativeIntent && { creativeIntent }),
        ...(visualBible && { visualBible }),
        ...(productionWorkflow && { productionWorkflow }),
      })
      .returning();

    // Observability only: recordCreationEvent swallows its own failures, so creation never depends on it.
    recordCreationEvent({
      projectId: newProject[0].id,
      kind: "project_created",
      payload: {
        inputMode: creationBrief.inputMode,
        outputStrategy: creationBrief.outputStrategy,
        audioStrategy: creationBrief.audioStrategy,
        styleType: creationBrief.styleType,
        styleSource: creationBrief.styleSource,
        sourceType: sourceType ?? "manual",
      },
    });

    return NextResponse.json(newProject[0], { status: 201 });
  } catch (error) {
    console.error("创建项目失败:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "创建项目失败" },
      { status: 500 }
    );
  }
}
