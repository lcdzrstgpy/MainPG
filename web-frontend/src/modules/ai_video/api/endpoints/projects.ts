import { cfJson, type CfInit } from "../clipforgeClient";
import type { Project, ProjectCreateInput, ProjectPatch, WorkItem } from "../types";

/** GET /api/project —— 最近编辑在前 */
export function listProjects(init?: CfInit) {
  return cfJson<Project[]>("/api/project", init);
}

export function getProject(id: string, init?: CfInit) {
  return cfJson<Project>(`/api/project/${encodeURIComponent(id)}`, init);
}

export function createProject(input: ProjectCreateInput, init?: CfInit) {
  return cfJson<Project>("/api/project", { ...init, method: "POST", json: input });
}

export function patchProject(id: string, patch: ProjectPatch, init?: CfInit) {
  return cfJson<Project>(`/api/project/${encodeURIComponent(id)}`, {
    ...init,
    method: "PATCH",
    json: patch,
  });
}

export function deleteProject(id: string, init?: CfInit) {
  return cfJson<{ success: boolean }>(`/api/project/${encodeURIComponent(id)}`, {
    ...init,
    method: "DELETE",
  });
}

/** GET /api/works —— 跨项目成片 feed */
export function listWorks(init?: CfInit) {
  return cfJson<{ works: WorkItem[] }>("/api/works", init);
}
