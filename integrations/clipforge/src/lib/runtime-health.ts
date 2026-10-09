/**
 * MainPG sidecar runtime health contract.
 *
 * MainPG 只有在这份 payload 完全合规时才会把 sidecar 判为 ready，因此这里必须
 * 保持"纯函数 + 稳定错误码"：不读取环境、不泄露绝对路径、不做 I/O。
 * 路由层（src/app/api/health/route.ts）负责把真实的数据库/迁移/目录/媒体探针
 * 结果收敛成这里的五个 RuntimeCheck。
 */

export type RuntimeCheck =
  | { status: "ok" }
  | { status: "error"; code: string };

export type RuntimeHealthInput = {
  instanceId: string;
  database: RuntimeCheck;
  migrations: RuntimeCheck;
  dataDirWritable: RuntimeCheck;
  ffmpeg: RuntimeCheck;
  ffprobe: RuntimeCheck;
};

export type RuntimeHealthPayload = {
  service: "clipforge";
  schemaVersion: 1;
  instanceId: string;
  status: "ok" | "error";
  checks: Omit<RuntimeHealthInput, "instanceId">;
};

export function buildRuntimeHealth(input: RuntimeHealthInput): RuntimeHealthPayload {
  const { instanceId, ...checks } = input;
  const status = Object.values(checks).every((check) => check.status === "ok") ? "ok" : "error";
  return { service: "clipforge", schemaVersion: 1, instanceId, status, checks };
}
