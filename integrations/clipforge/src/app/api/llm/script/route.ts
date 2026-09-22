import { NextRequest, NextResponse } from "next/server";
import { getDataDir } from "@/lib/paths";
import { readFile } from "fs/promises";
import { join } from "path";
import { generateScript, analyzeProduct } from "@/lib/script-engine/generator";
import { styleNameMap, resolveNarrative, type ScriptStyleType } from "@/lib/script-engine/prompts";
import { hookPatternName, HOOK_PATTERNS } from "@/lib/script-engine/hook-patterns";
import type { ProductCategory } from "@/lib/script-engine/templates";
import { getDb } from "@/lib/db";
import { scripts as scriptsTable, projects, publishMetrics } from "@/lib/db/schema";
import { eq } from "drizzle-orm";
import { apiError, errText } from "@/lib/api-error";
import { llmErrorPair } from "@/lib/llm-error";
import { resolveScriptStyle } from "@/lib/script-style";
import type { CreationBrief } from "@/lib/creation-brief";
import type { CreativeIntent, VisualBible } from "@/lib/production-system";
import { topConvertingStyle, topConvertingHook, buildPerformanceHint, type MetricInput } from "@/lib/performance-insights";

/** Allowed enum values for the styleType column in the scripts table */
const SCRIPT_STYLE_TYPES = [
  "pain_point", "scene", "comparison", "story",
  "drama", "reversal", "interview", "unboxing", "product_pov", "talking_head",
  "custom",
] as const;
const VALID_SCRIPT_STYLE: ReadonlySet<string> = new Set(SCRIPT_STYLE_TYPES);

/**
 * UI whitelist style → engine ScriptStyleType (total over the selectable styles). Only two spellings
 * differ between the creation whitelist (ad-templates STYLE_VALUES) and the script engine; the rest
 * are identical in both vocabularies. "auto" is deliberately absent: it is resolved — or refused —
 * by `resolveScriptStyle` before generation, never mapped to a style here.
 */
const ENGINE_STYLE_BY_UI_STYLE: Readonly<Record<string, ScriptStyleType>> = {
  "pain-point": "pain_point",
  scenario: "scene",
  comparison: "comparison",
  story: "story",
  drama: "drama",
  reversal: "reversal",
  interview: "interview",
  unboxing: "unboxing",
  product_pov: "product_pov",
  talking_head: "talking_head",
};

/** Non-empty trimmed string, or undefined — an empty string counts as "the caller sent nothing". */
const explicitText = (value: unknown): string | undefined =>
  typeof value === "string" && value.trim() ? value : undefined;

/**
 * Design §7.3: script generation falls back to the project's persisted creation settings when the
 * request omits a field. Best-effort read — a missing project or a DB failure degrades to "no
 * defaults" instead of turning script generation into a 500.
 */
interface ProjectDefaults {
  brief: CreationBrief | null;
  creativeIntent: CreativeIntent | null;
  visualBible: VisualBible | null;
}

async function readProjectDefaults(projectId: string): Promise<ProjectDefaults> {
  try {
    const db = getDb();
    const rows = await db
      .select({
        creationBrief: projects.creationBrief,
        creativeIntent: projects.creativeIntent,
        visualBible: projects.visualBible,
      })
      .from(projects)
      .where(eq(projects.id, projectId));
    const row = rows[0];
    return {
      brief: row?.creationBrief ?? null,
      creativeIntent: row?.creativeIntent ?? null,
      visualBible: row?.visualBible ?? null,
    };
  } catch (e) {
    console.warn("读取项目创作设定失败（已跳过默认值）:", e);
    return { brief: null, creativeIntent: null, visualBible: null };
  }
}

/** Convert a local image path to a base64 data URI for use with LLM vision models */
async function imagePathToBase64(imagePath: string): Promise<string> {
  // Already a full URL or base64 data URI, return as-is
  if (imagePath.startsWith("http") || imagePath.startsWith("data:")) {
    return imagePath;
  }

  // Local API path e.g. /api/files/projectId/filename.png
  // Extract the actual file path: data/uploads/projectId/filename.png
  const match = imagePath.match(/\/api\/files\/(.+)/);
  if (!match) return imagePath;

  const relativePath = match[1];
  const filePath = join(getDataDir(), "uploads", relativePath);

  try {
    const buffer = await readFile(filePath);
    const base64 = buffer.toString("base64");
    // Infer MIME type from file extension
    const ext = filePath.split(".").pop()?.toLowerCase() || "png";
    const mimeMap: Record<string, string> = {
      jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png",
      webp: "image/webp", gif: "image/gif", svg: "image/svg+xml",
    };
    const mime = mimeMap[ext] || "image/png";
    return `data:${mime};base64,${base64}`;
  } catch {
    console.warn(`无法读取图片文件: ${filePath}`);
    return imagePath;
  }
}

/** Normalize a frontend category value to a ProductCategory supported by the engine */
function normalizeCategory(raw: unknown): ProductCategory {
  const map: Record<string, ProductCategory> = {
    beauty: "beauty",
    food: "food",
    home: "home",
    fashion: "fashion",
    tech: "tech",
    digital: "tech", // frontend uses "digital" for the "Electronics/3C" category
    "3c": "tech",
    other: "beauty", // fallback for uncategorized items
  };
  return map[String(raw ?? "").toLowerCase()] ?? "beauty";
}

/**
 * Data-flywheel read side: turn the creator's real published-video metrics into a generation hint.
 * Prefers same-category conversion signal, falls back to the global aggregate when a category lacks
 * enough samples, and degrades to an empty hint on cold start or any DB error (never blocks generation).
 * Returns the hint text plus the top-converting style key and its sample count — the count gates the
 * "auto"/smart-recommend decision, so a thin sample never turns into a silent style guess.
 */
async function loadInsights(category: string): Promise<{ hint: string; topStyle: string | null; sampleSize: number }> {
  try {
    const db = getDb();
    const rows = await db.select().from(publishMetrics);
    if (rows.length === 0) return { hint: "", topStyle: null, sampleSize: 0 };
    const toRec = (r: (typeof rows)[number]): MetricInput => ({
      style: r.style,
      hookId: r.hookId ?? undefined,
      views: r.views,
      likes: r.likes,
      comments: r.comments,
      shares: r.shares,
      orders: r.orders,
    });
    const scoped = rows.filter((r) => r.category === category).map(toRec);
    const all = rows.map(toRec);
    // same-category signal first (topConvertingStyle/Hook require >=2 samples and return null otherwise),
    // then global fallback so a creator with cross-category history still gets a useful prior
    const topStyleInsight = topConvertingStyle(scoped) ?? topConvertingStyle(all);
    const topHook = topConvertingHook(scoped) ?? topConvertingHook(all);
    const hint = buildPerformanceHint(topStyleInsight, topHook, {
      styleLabel: (s) => styleNameMap[s as ScriptStyleType] ?? s,
      hookLabel: hookPatternName,
    });
    return {
      hint,
      topStyle: topStyleInsight?.style ?? null,
      sampleSize: topStyleInsight?.samples ?? 0,
    };
  } catch (e) {
    // Feedback is best-effort — never let a metrics read failure break script generation
    console.warn("读取历史转化数据失败（已跳过反馈）:", e);
    return { hint: "", topStyle: null, sampleSize: 0 };
  }
}

// Generate commerce script
export async function POST(req: NextRequest) {
  const body = await req.json();
  const {
    productImages,
    productName,
    productDescription,
    llmConfig,
  } = body;

  // Support both frontend field naming conventions: category/productCategory, targetDuration/duration
  const category = normalizeCategory(body.category ?? body.productCategory);
  const duration = body.targetDuration ?? body.duration ?? 30;
  // data flywheel: performance feedback is on by default; pass insightMode:false to opt out
  const useInsights = body.insightMode !== false;

  // §7.3: the project's persisted creation settings supply defaults (style / audience / platforms /
  // narrative / creative intent / visual bible) for callers that omit a field; an explicit request
  // field always wins.
  const projectId = body.projectId;
  const defaults = typeof projectId === "string" && projectId
    ? await readProjectDefaults(projectId)
    : { brief: null, creativeIntent: null, visualBible: null };
  const brief = defaults.brief;
  // Narrative: the request body wins field by field, the project brief fills the gaps.
  const narrative = resolveNarrative(body.narrative, brief?.narrative);
  const requestedStyle = explicitText(body.styleType) ?? explicitText(brief?.styleType) ?? "";
  const briefAudience = Array.isArray(brief?.targetAudience) && brief.targetAudience.length
    ? brief.targetAudience.join(",")
    : undefined;
  const briefPlatforms = Array.isArray(brief?.platforms) && brief.platforms.length
    ? brief.platforms.join(",")
    : undefined;

  if (!productName) {
    return apiError(req, "请填写商品名称", "Please enter the product name");
  }

  if (!llmConfig?.baseUrl || !llmConfig?.apiKey || !llmConfig?.model) {
    return apiError(req, "请配置 LLM 参数（baseUrl、apiKey、model）", "Please configure the LLM parameters (baseUrl, apiKey, model)");
  }

  const targetAudience = explicitText(body.targetAudience) ?? briefAudience;
  const platforms = explicitText(body.platforms) ?? briefPlatforms;

  // Data flywheel (read side): pull the creator's real conversion feedback for this category. It is
  // used two ways: (1) as the only admissible source for smart-recommend ("auto") style resolution,
  // (2) as an advisory hint injected into the prompt so variants lean toward what actually sells.
  const insights = useInsights ? await loadInsights(category) : { hint: "", topStyle: null, sampleSize: 0 };

  // §5.3 / Task 4.1: resolve the style without ever guessing. A template-supplied style is recorded
  // as `template`, a creator choice as `explicit`; "auto" resolves only with enough historical
  // evidence (`performance-recommendation`) and is otherwise refused instead of silently becoming
  // pain-point. Unknown values are refused as well.
  const resolution = resolveScriptStyle({
    requestedStyle,
    styleSource: explicitText(body.templateId) ? "template" : null,
    insights: { topStyle: insights.topStyle, sampleSize: insights.sampleSize },
  });

  if (resolution.kind === "needs-explicit-choice") {
    const noData = resolution.reason === "no-data";
    return NextResponse.json(
      {
        error: errText(
          req,
          noData
            ? "历史转化数据不足，无法智能推荐脚本风格，请显式选择一种风格后重试"
            : "无法识别该脚本风格，请从候选风格中选择一种后重试",
          noData
            ? "Not enough historical performance data to recommend a script style — please choose one explicitly and retry"
            : "Unknown script style — please pick one of the candidate styles and retry"
        ),
        code: "needs_explicit_style",
        reason: resolution.reason,
        candidates: resolution.candidates,
      },
      { status: 409 }
    );
  }

  // The resolved value is a UI whitelist style; the engine and the scripts table speak engine keys.
  const styleType = ENGINE_STYLE_BY_UI_STYLE[resolution.styleType];

  try {
    // Product image analysis: convert local paths to base64 before passing to the vision model
    let analysis = body.productAnalysis;
    if (!analysis && productImages?.length > 0 && llmConfig) {
      try {
        const imageUrls = await Promise.all(
          (productImages as string[]).map(imagePathToBase64)
        );
        analysis = await analyzeProduct(imageUrls, llmConfig);
      } catch (e) {
        // Image analysis failure should not block script generation
        console.warn("商品图片分析失败（已跳过）:", e);
      }
    }

    // Generate script (category/styleType/duration already normalized above)
    const scripts = await generateScript({
      productName,
      category,
      productDescription,
      productAnalysis: analysis,
      styleType,
      targetDuration: duration,
      videoMode: body.videoMode,
      priceRange: body.priceRange,
      platforms,
      usageAdvantage: body.usageAdvantage,
      targetAudience,
      referenceStructure: body.referenceStructure,
      // ad-template creative direction (look + per-shot-type camera plan) and any other
      // caller-supplied requirements — buildUserPrompt already injects this field
      customRequirements: typeof body.customRequirements === "string" ? body.customRequirements.slice(0, 2000) : undefined,
      performanceHint: insights.hint,
      // §7.3: the creator's narrative requirements and the project's visual constraints (creative
      // intent + visual bible) must reach the LLM, not just live in the project row.
      narrative,
      creativeIntent: defaults.creativeIntent ?? undefined,
      visualBible: defaults.visualBible ?? undefined,
      // anti-homogenization: batch rotation pins a different opening hook mechanism per video (validated against the pattern library)
      preferredHookId:
        typeof body.preferredHookId === "string" && HOOK_PATTERNS.some((p) => p.id === body.preferredHookId)
          ? body.preferredHookId
          : undefined,
      llmConfig,
    });

    // Persist: write generated scripts to the scripts table so the script/assets pages can read them by projectId
    let savedScripts = scripts;
    if (projectId) {
      const db = getDb();
      // Refuse to overwrite a one-liner topic project with a commerce script (contentType mismatch — would delete its topic scripts)
      const proj = await db
        .select({ contentType: projects.contentType })
        .from(projects)
        .where(eq(projects.id, projectId));
      if (proj.length > 0 && proj[0].contentType === "topic") {
        return NextResponse.json(
          { error: errText(req, "该项目是一句话主题项目，请勿用带货脚本覆盖", "This project is a one-sentence topic project — do not overwrite it with a commerce script"), projectId },
          { status: 409 }
        );
      }
      try {
        // Delete existing scripts for this project first (overwrite on regenerate)
        await db.delete(scriptsTable).where(eq(scriptsTable.projectId, projectId));
        const rows = await db
          .insert(scriptsTable)
          .values(
            scripts.map((s, i) => ({
              projectId,
              version: 1,
              styleType: (VALID_SCRIPT_STYLE.has(s.styleType) ? s.styleType : "custom") as
                | "pain_point" | "scene" | "comparison" | "story"
                | "drama" | "reversal" | "interview" | "unboxing" | "product_pov" | "talking_head"
                | "custom",
              title: s.title,
              totalDuration: s.totalDuration,
              shots: s.shots,
              // dialogue-script cast (drama style) — read back at compose time for multi-voice TTS
              ...(s.characters?.length && { characters: s.characters }),
              selected: i === 0, // select the first script set by default
            }))
          )
          .returning();
        savedScripts = rows.map((r) => ({
          id: r.id,
          title: r.title ?? "",
          styleType: r.styleType,
          totalDuration: r.totalDuration ?? 0,
          shots: r.shots ?? [],
          selected: r.selected ?? false,
        })) as typeof scripts;
        // Sync project status and analysis result. When the project already carries a creation
        // brief, record the style actually used and its provenance there — the scripts table has no
        // provenance column and we must not add one, so the project brief is the existing field that
        // carries `styleSource` (projects created before the brief keep it null).
        const briefPatch = brief
          ? { creationBrief: { ...brief, styleType: resolution.styleType, styleSource: resolution.styleSource } }
          : {};
        await db
          .update(projects)
          .set({
            status: "scripting",
            ...(analysis && { productAnalysis: analysis }),
            ...briefPatch,
            updatedAt: new Date(),
          })
          .where(eq(projects.id, projectId));
      } catch (e) {
        // DB write failure must surface as an error — returning 200 would let the frontend navigate away thinking it succeeded, then read empty scripts from the DB (which may already have had their old scripts deleted)
        console.error("脚本落库失败:", e);
        return NextResponse.json({ error: errText(req, "脚本落库失败，请重试", "Failed to save scripts to the database, please try again"), projectId }, { status: 500 });
      }
    }

    // styleType is the resolved UI style (design §5.3 contract for the caller); savedScripts keep the
    // engine keys the scripts table stores.
    return NextResponse.json({
      scripts: savedScripts,
      analysis,
      styleType: resolution.styleType,
      styleSource: resolution.styleSource,
    });
  } catch (error) {
    console.error("脚本生成失败:", error);
    // LLM failures carry an actionable bilingual message (bad key / dead free endpoint / rate limit)
    const { zh, en } = llmErrorPair(error);
    return apiError(
      req,
      `脚本生成失败: ${zh}`,
      `Script generation failed: ${en}`,
      500
    );
  }
}
