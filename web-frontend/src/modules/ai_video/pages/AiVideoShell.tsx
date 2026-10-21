import { useCallback, useEffect } from "react";

import { AiVideoServiceState } from "../components/AiVideoServiceState";
import { useClipForgeService } from "../hooks/useClipForgeService";
import { AI_VIDEO_NAV, aiVideoNavHrefForPath, type AiVideoNavHref } from "../nav";

export type { AiVideoNavHref };
import { canEmbedClipForge } from "../state/clipforgeServiceState";
import { BatchPage } from "./BatchPage";
import { ClonePage } from "./ClonePage";
import { PresentersPage } from "./PresentersPage";
import { ProductsPage } from "./ProductsPage";
import { ProjectAssetsPage } from "./ProjectAssetsPage";
import { ProjectExportPage } from "./ProjectExportPage";
import { ProjectProductionPage } from "./ProjectProductionPage";
import { ProjectScriptPage } from "./ProjectScriptPage";
import { ProjectTranscriptPage } from "./ProjectTranscriptPage";
import { ProjectVideoPage } from "./ProjectVideoPage";
import { ProjectsPage } from "./ProjectsPage";
import { SettingsPage } from "./SettingsPage";
import { StartPage } from "./StartPage";

// 项目详情里已定义的流水线步骤（全部原生）。
export const AI_VIDEO_PROJECT_STEPS = [
  "script",
  "assets",
  "video",
  "export",
  "production",
  "transcript",
] as const;

export type AiVideoProjectStep = (typeof AI_VIDEO_PROJECT_STEPS)[number];

/** 模块内路径：7 个一级入口 + 项目详情深层路径。 */
export type AiVideoPath = AiVideoNavHref | `/project/${string}/${AiVideoProjectStep}`;

const PROJECT_ROUTE = /^\/project\/[A-Za-z0-9-]+\/(?:script|assets|video|export|production|transcript)$/;

const PROJECT_SCRIPT_ROUTE = /^\/project\/([A-Za-z0-9-]+)\/script$/;
const PROJECT_ASSETS_ROUTE = /^\/project\/([A-Za-z0-9-]+)\/assets$/;
const PROJECT_VIDEO_ROUTE = /^\/project\/([A-Za-z0-9-]+)\/video$/;
const PROJECT_EXPORT_ROUTE = /^\/project\/([A-Za-z0-9-]+)\/export$/;
const PROJECT_PRODUCTION_ROUTE = /^\/project\/([A-Za-z0-9-]+)\/production$/;
const PROJECT_TRANSCRIPT_ROUTE = /^\/project\/([A-Za-z0-9-]+)\/transcript$/;

/** 只接受模块内已知路径；未知路径一律回落到 `/start`。 */
export function isAiVideoPath(value: unknown): value is AiVideoPath {
  if (typeof value !== "string") return false;
  if (PROJECT_ROUTE.test(value)) return true;
  return AI_VIDEO_NAV.some((item) => item.href === value);
}

/** 外层侧栏点击下发的跳转请求：nonce 保证同一入口重复点击也能再次生效。 */
export type AiVideoNavRequest = { href: AiVideoNavHref; nonce: number };

type AiVideoShellProps = {
  /** 当前页面路径（来自页签状态，刷新后由 workspaceTabsPersistence 恢复）。 */
  path?: string;
  request?: AiVideoNavRequest | null;
  /** 原生页面内换页时上报真实路径，外层写回页签。 */
  onPathChange?: (path: AiVideoPath) => void;
  onActiveHrefChange?: (href: AiVideoNavHref) => void;
};

/**
 * AI 视频模块外壳：所有路径都已原生化，这里只负责服务就绪门禁与页面分发。
 * sidecar 未就绪时先展示启动/诊断状态，避免页面在服务不可用时直接报错。
 */
export function AiVideoShell({ path = "/start", request = null, onPathChange, onActiveHrefChange }: AiVideoShellProps) {
  const { status, error, start } = useClipForgeService();
  const currentPath: AiVideoPath = isAiVideoPath(path) ? path : "/start";

  const handleNativeNavigate = useCallback(
    (next: string) => {
      if (!isAiVideoPath(next)) return;
      const href = aiVideoNavHrefForPath(next);
      if (href) onActiveHrefChange?.(href);
      onPathChange?.(next);
    },
    [onActiveHrefChange, onPathChange],
  );

  // 外层侧栏点击：直接切换当前模块路径（没有 iframe 需要转发消息）。
  useEffect(() => {
    if (!request || !isAiVideoPath(request.href)) return;
    const href = aiVideoNavHrefForPath(request.href);
    if (href) onActiveHrefChange?.(href);
    onPathChange?.(request.href);
  }, [onActiveHrefChange, onPathChange, request]);

  // 页面首次进入时同步一次侧栏高亮（刷新后从页签恢复的深层路径也能对上）。
  useEffect(() => {
    const href = aiVideoNavHrefForPath(currentPath);
    if (href) onActiveHrefChange?.(href);
  }, [currentPath, onActiveHrefChange]);

  if (!canEmbedClipForge(status) || !status?.url) {
    return <AiVideoServiceState status={status} requestError={error} onStart={start} />;
  }

  const projectScript = currentPath.match(PROJECT_SCRIPT_ROUTE);
  if (projectScript) {
    return <ProjectScriptPage projectId={projectScript[1]} onNavigate={handleNativeNavigate} />;
  }

  const projectAssets = currentPath.match(PROJECT_ASSETS_ROUTE);
  if (projectAssets) {
    return <ProjectAssetsPage projectId={projectAssets[1]} onNavigate={handleNativeNavigate} />;
  }

  const projectVideo = currentPath.match(PROJECT_VIDEO_ROUTE);
  if (projectVideo) {
    return <ProjectVideoPage projectId={projectVideo[1]} onNavigate={handleNativeNavigate} />;
  }

  const projectExport = currentPath.match(PROJECT_EXPORT_ROUTE);
  if (projectExport) {
    return <ProjectExportPage projectId={projectExport[1]} onNavigate={handleNativeNavigate} />;
  }

  const projectProduction = currentPath.match(PROJECT_PRODUCTION_ROUTE);
  if (projectProduction) {
    return <ProjectProductionPage projectId={projectProduction[1]} onNavigate={handleNativeNavigate} />;
  }

  const projectTranscript = currentPath.match(PROJECT_TRANSCRIPT_ROUTE);
  if (projectTranscript) {
    return <ProjectTranscriptPage projectId={projectTranscript[1]} onNavigate={handleNativeNavigate} />;
  }

  if (currentPath === "/start") {
    return <StartPage onNavigate={handleNativeNavigate} />;
  }

  if (currentPath === "/projects") {
    return <ProjectsPage onNavigate={handleNativeNavigate} />;
  }

  if (currentPath === "/products") {
    return <ProductsPage onNavigate={handleNativeNavigate} />;
  }

  if (currentPath === "/presenters") {
    return <PresentersPage onNavigate={handleNativeNavigate} />;
  }

  if (currentPath === "/project/clone") {
    return <ClonePage onNavigate={handleNativeNavigate} />;
  }

  if (currentPath === "/batch") {
    return <BatchPage onNavigate={handleNativeNavigate} />;
  }

  if (currentPath === "/settings") {
    return <SettingsPage />;
  }

  return <StartPage onNavigate={handleNativeNavigate} />;
}
