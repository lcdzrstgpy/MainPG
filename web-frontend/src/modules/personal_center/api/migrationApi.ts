import { getAuthToken, httpJson } from "../../../transport/http/client";

/** 迁移包内容清单：个人中心的「数据迁移」卡片用它告诉用户这一包会带走什么。 */
export type MigrationPlan = {
  format: string;
  schema_version: number;
  data_dir: string;
  database: {
    path: string;
    bytes: number;
    /** 参与迁移的表数量（已扣除被剥离的凭证类表）。 */
    tables: number;
    rows: number;
    includes: string[];
    /** 导出时会被清空、不随迁移包走的表（登录会话 / 密钥 / 机器指纹授权等）。 */
    scrubbed: string[];
  };
  assets: Array<{ group: string; path: string; files: number; bytes: number }>;
  asset_files: number;
  asset_bytes: number;
  total_bytes: number;
  total_files: number;
  /** 产品库用了自定义保存目录（换机时会按原路径还原）。 */
  custom_profit_root: boolean;
  /** 已导入、等下次启动生效的迁移包。 */
  pending: {
    pending: true;
    staged_at?: string;
    asset_files?: number;
    asset_errors?: number;
    asset_overwritten?: number;
    /** 包的来源版本与本机不一致时的提示（不阻断导入）。 */
    version_notice?: string;
    /** 产品库保存目录被改指到本机默认目录的说明。 */
    save_root_notice?: string;
    /** 上次启动尝试替换数据库失败的原因；非空说明「重启也没生效」。 */
    apply_error?: string;
  } | null;
};

export type MigrationJob = {
  job_id: string;
  kind: "export" | "import";
  state: "running" | "done" | "failed";
  phase: string;
  done: number;
  total: number;
  message: string;
  result: {
    path?: string;
    bytes?: number;
    asset_files?: number;
    /** 打包时读不了、被跳过的素材文件数（>0 说明这一包不完整）。 */
    skipped_assets?: number;
    asset_errors?: number;
    asset_overwritten?: number;
    version_notice?: string;
    /** 旧机的产品库保存目录在新机不可用时，被改指到本机默认目录的说明。 */
    save_root_notice?: string;
    restart_required?: boolean;
  } | null;
};

export function fetchMigrationPlan(): Promise<MigrationPlan> {
  return httpJson<MigrationPlan>("/api/migration/plan");
}

export function startMigrationExport(): Promise<MigrationJob> {
  return httpJson<MigrationJob>("/api/migration/export", { method: "POST" });
}

export function fetchMigrationJob(kind: "export" | "import", jobId: string): Promise<MigrationJob> {
  return httpJson<MigrationJob>(`/api/migration/${kind}/${jobId}`);
}

/** 迁移包下载地址（同源相对路径，开发态由 vite 代理到 8010）。 */
export function migrationDownloadUrl(jobId: string): string {
  return `/api/migration/export/${jobId}/download`;
}

/**
 * 上传迁移包。
 *
 * 走裸 fetch + FormData（httpJson 固定 content-type: application/json，发不了文件），
 * 但超时同样要放宽：几 GB 的包在慢盘上远不止默认的 30 秒。
 */
export async function uploadMigrationArchive(file: File): Promise<MigrationJob> {
  const body = new FormData();
  body.append("archive", file);
  const token = getAuthToken();
  const response = await fetch("/api/migration/import", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body,
  });
  const payload = (await response.json().catch(() => ({}))) as {
    detail?: unknown;
    job_id?: string;
  };
  if (!response.ok) {
    const detail = payload?.detail;
    throw new Error(typeof detail === "string" && detail.trim() ? detail : "迁移包上传失败");
  }
  return payload as MigrationJob;
}
