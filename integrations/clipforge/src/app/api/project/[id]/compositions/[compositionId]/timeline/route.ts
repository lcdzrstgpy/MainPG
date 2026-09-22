import { NextRequest, NextResponse } from "next/server";
import { and, eq } from "drizzle-orm";
import { readFile } from "fs/promises";
import { join, normalize, sep } from "path";
import { apiError, errText } from "@/lib/api-error";
import { getDb } from "@/lib/db";
import { compositions } from "@/lib/db/schema";
import { fileNameOf, getOutputDir } from "@/lib/paths";

export const runtime = "nodejs";

const SAFE_ID = /^[a-zA-Z0-9-]+$/;
/** 成片文件名：只接受裸文件名（不含分隔符与 `..`），避免库里的历史脏路径拼出目录穿越 */
const SAFE_FILE_NAME = /^[a-zA-Z0-9._-]+$/;

/**
 * 读取 sidecar 内容：只读 `<项目 output 目录>/<成片文件名>.timeline.json`，且必须落在该目录内。
 * 读不到（老成片没有 sidecar / 文件被删 / JSON 损坏）时返回 null，交给调用方区分「未生成」。
 */
async function readTimelineSidecar(projectId: string, fileName: string): Promise<unknown | null> {
  const projectOutputDir = join(getOutputDir(), projectId);
  const sidecarPath = normalize(join(projectOutputDir, `${fileName}.timeline.json`));
  if (!sidecarPath.startsWith(projectOutputDir + sep)) return null;
  try {
    const parsed: unknown = JSON.parse(await readFile(sidecarPath, "utf8"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * GET /api/project/[id]/compositions/[compositionId]/timeline — 按成片版本读取它的 timeline sidecar
 * （含逐镜音频报告 `voiceReport`）。
 *
 * 为什么需要它：`GET /api/project/[id]/compose` 只能给出「最新一条」合成记录，导出页选中的历史版本
 * 因此看不到自己的音频报告（计划「未决事项」第 6 条）。这里按 compositionId 精确读取。
 *
 * 契约：
 * - 200 `{ compositionId, status: "ready", fileName, timelineUrl, timeline }` —— sidecar 已生成；
 * - 200 `{ compositionId, status: "missing", fileName, timelineUrl: null, timeline: null }` —— 成片属于本项目，
 *   但 sidecar 尚未生成（老成片 / 还在合成中），与「成片不存在」明确区分；
 * - 400 项目 id 或成片 id 非法；404 该成片不存在或不属于本项目。
 *
 * 安全：只返回文件名与 `/api/output/...` 形式的相对 URL，绝不回传本机绝对路径。
 */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; compositionId: string }> }
) {
  try {
    const { id, compositionId } = await params;
    if (!SAFE_ID.test(id) || !SAFE_ID.test(compositionId)) {
      return apiError(req, "无效的成片ID", "Invalid composition ID");
    }

    const db = getDb();
    const rows = await db
      .select({ id: compositions.id, outputPath: compositions.outputPath })
      .from(compositions)
      .where(and(eq(compositions.projectId, id), eq(compositions.id, compositionId)))
      .limit(1);
    const composition = rows[0];
    if (!composition) return apiError(req, "成片不存在", "Composition not found", 404);

    // separator-agnostic: Windows rows store backslash absolute paths (issue #15)
    const fileName = fileNameOf(composition.outputPath);
    const timeline = fileName && SAFE_FILE_NAME.test(fileName)
      ? await readTimelineSidecar(id, fileName)
      : null;

    return NextResponse.json({
      compositionId,
      status: timeline ? "ready" : "missing",
      fileName: fileName || null,
      timelineUrl: timeline ? `/api/output/${id}/${encodeURIComponent(`${fileName}.timeline.json`)}` : null,
      timeline,
    });
  } catch (error) {
    console.error("获取成片音频报告失败:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : errText(req, "获取成片音频报告失败", "Failed to read the composition timeline") },
      { status: 500 }
    );
  }
}
