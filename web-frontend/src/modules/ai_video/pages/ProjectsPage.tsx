import { useEffect, useMemo, useState } from "react";

import { deleteProject, listProjects, listWorks } from "../api/endpoints/projects";
import type { Project, WorkItem } from "../api/types";
import { aiVideoMediaUrl } from "../lib/media-url";
import { formatRelativeTime } from "../lib/relative-time";

type ProjectsPageProps = {
  onNavigate: (path: string) => void;
};

type View = "projects" | "works";

/** 项目状态 → 续做步骤（已出片的项目落在导出页，成片就在那里）。 */
function stepFor(status: Project["status"]): string {
  if (status === "done") return "export";
  if (status === "composing" || status === "video") return "video";
  if (status === "assets") return "assets";
  return "script";
}

function statusLabel(status: Project["status"]): string {
  switch (status) {
    case "done":
      return "已出片";
    case "composing":
      return "合成中";
    case "video":
      return "视频中";
    case "assets":
      return "素材中";
    case "scripting":
      return "脚本中";
    default:
      return "草稿";
  }
}

function timestampOf(value: string | null | undefined): number {
  if (!value) return 0;
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function downloadHref(url: string): string | null {
  const base = aiVideoMediaUrl(url);
  if (!base) return null;
  return `${base}${base.includes("?") ? "&" : "?"}download=1`;
}

/** 我的项目：项目库（搜索 / 续做 / 删除）+ 跨项目成片 feed。 */
export function ProjectsPage({ onNavigate }: ProjectsPageProps) {
  const [rows, setRows] = useState<Project[]>([]);
  const [works, setWorks] = useState<WorkItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [view, setView] = useState<View>("projects");

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [projects, worksResult] = await Promise.all([
          listProjects(),
          listWorks().catch(() => ({ works: [] as WorkItem[] })),
        ]);
        if (cancelled) return;
        setRows([...projects].sort((a, b) => timestampOf(b.updatedAt) - timestampOf(a.updatedAt)));
        setWorks(worksResult.works ?? []);
      } catch (cause) {
        if (!cancelled) setLoadError(cause instanceof Error ? cause.message : "项目列表加载失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  // 成片按时间倒序，同一项目第一次出现的缩略图就是最新成片的海报。
  const posterByProject = useMemo(() => {
    const map = new Map<string, string>();
    for (const work of works) {
      if (work.thumbnailUrl && !map.has(work.projectId)) map.set(work.projectId, work.thumbnailUrl);
    }
    return map;
  }, [works]);

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return rows;
    return rows.filter(
      (project) =>
        project.name.toLowerCase().includes(keyword) ||
        (project.productName ?? "").toLowerCase().includes(keyword),
    );
  }, [rows, query]);

  async function handleDelete(project: Project) {
    const name = project.name || project.productName || "未命名项目";
    if (!window.confirm(`确定删除「${name}」吗？项目中的脚本、素材与成片记录都会删除，且无法恢复。`)) return;
    setDeletingId(project.id);
    setDeleteError(null);
    try {
      await deleteProject(project.id);
      setRows((current) => current.filter((item) => item.id !== project.id));
      setWorks((current) => current.filter((work) => work.projectId !== project.id));
    } catch (cause) {
      setDeleteError(cause instanceof Error ? cause.message : "删除失败，请稍后重试");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <section className="ai-video-page" aria-label="我的项目">
      <header className="ai-video-module-header">
        <div>
          <p className="ai-video-eyebrow">AI 创作工具</p>
          <h2>我的项目</h2>
          <p>所有项目的脚本、素材与成片都在这里；点开卡片会回到对应步骤继续。</p>
        </div>
        <button type="button" className="ai-video-header-action" onClick={() => onNavigate("/start")}>
          新建项目
        </button>
      </header>

      <div className="ai-video-toolbar">
        <div className="ai-video-segmented" role="group" aria-label="项目视图">
          <button
            type="button"
            className={view === "projects" ? "is-active" : ""}
            aria-pressed={view === "projects"}
            onClick={() => setView("projects")}
          >
            项目 {rows.length > 0 && <span>{rows.length}</span>}
          </button>
          <button
            type="button"
            className={view === "works" ? "is-active" : ""}
            aria-pressed={view === "works"}
            onClick={() => setView("works")}
          >
            成片 {works.length > 0 && <span>{works.length}</span>}
          </button>
        </div>
        {view === "projects" && rows.length > 0 && (
          <input
            type="search"
            className="ai-video-search"
            value={query}
            placeholder="搜索项目名 / 商品名"
            onChange={(event) => setQuery(event.target.value)}
          />
        )}
      </div>

      {deleteError && (
        <div className="ai-video-form-error" role="alert">
          {deleteError}
        </div>
      )}

      {loading ? (
        <div className="ai-video-empty" role="status">
          <span className="ai-video-empty-icon">◌</span>
          <strong>正在加载项目…</strong>
        </div>
      ) : loadError ? (
        <div className="ai-video-empty" role="alert">
          <span className="ai-video-empty-icon">!</span>
          <strong>{loadError}</strong>
          <button type="button" onClick={() => window.location.reload()}>重新加载</button>
        </div>
      ) : view === "works" ? (
        works.length === 0 ? (
          <div className="ai-video-empty">
            <span className="ai-video-empty-icon">▷</span>
            <strong>还没有成片</strong>
            <p>项目合成成功后，成片会自动出现在这里，按封面就能找到它。</p>
            <button type="button" onClick={() => onNavigate("/start")}>去创建视频</button>
          </div>
        ) : (
          <>
            <p className="ai-video-list-hint">共 {works.length} 条成片，点击封面打开导出页。</p>
            <div className="ai-video-works-grid">
              {works.map((work) => {
                const poster = aiVideoMediaUrl(work.thumbnailUrl);
                const download = downloadHref(work.url);
                return (
                  <article key={work.id} className="ai-video-work-card">
                    <button
                      type="button"
                      className="ai-video-work-poster"
                      onClick={() => onNavigate(`/project/${work.projectId}/export`)}
                      aria-label={`打开 ${work.productName || work.projectName || "成片"}`}
                    >
                      {poster ? (
                        <img src={poster} alt="" loading="lazy" />
                      ) : (
                        <span className="ai-video-work-placeholder">▷</span>
                      )}
                      {work.label && <em>{work.label}</em>}
                    </button>
                    <div className="ai-video-work-meta">
                      <div>
                        <strong>{work.productName || work.projectName || "未命名成片"}</strong>
                        <span>{formatRelativeTime(work.createdAt, "zh")}</span>
                      </div>
                      {download && (
                        <a href={download} download title="下载成片">
                          下载
                        </a>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          </>
        )
      ) : rows.length === 0 ? (
        <div className="ai-video-empty">
          <span className="ai-video-empty-icon">▣</span>
          <strong>还没有项目</strong>
          <p>从商品图、商品链接或一句话主题开始，创建第一个视频项目。</p>
          <button type="button" onClick={() => onNavigate("/start")}>去视频工作台</button>
        </div>
      ) : filtered.length === 0 ? (
        <p className="ai-video-list-hint">没有匹配「{query.trim()}」的项目。</p>
      ) : (
        <div className="ai-video-projects-grid">
          {filtered.map((project) => {
            const poster = aiVideoMediaUrl(posterByProject.get(project.id) ?? project.productImages?.[0] ?? null);
            const step = stepFor(project.status);
            return (
              <article key={project.id} className="ai-video-project-card">
                <button
                  type="button"
                  className="ai-video-project-poster"
                  onClick={() => onNavigate(`/project/${project.id}/${step}`)}
                  aria-label={`继续 ${project.name}`}
                >
                  {poster ? (
                    <img src={poster} alt="" loading="lazy" />
                  ) : (
                    <span className="ai-video-project-placeholder">AI</span>
                  )}
                  <em className={`is-${project.status}`}>{statusLabel(project.status)}</em>
                </button>
                <div className="ai-video-project-meta">
                  <button type="button" onClick={() => onNavigate(`/project/${project.id}/${step}`)}>
                    <strong>{project.name || project.productName || "未命名项目"}</strong>
                    <span>
                      {project.productName || "无商品名"}
                      {" · "}
                      {formatRelativeTime(project.updatedAt, "zh")}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="ai-video-project-delete"
                    disabled={deletingId === project.id}
                    title="删除项目"
                    onClick={() => void handleDelete(project)}
                  >
                    {deletingId === project.id ? "…" : "删除"}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
