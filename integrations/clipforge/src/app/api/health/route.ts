import { NextResponse } from "next/server";
import { execFile } from "child_process";
import fs from "fs";
import { getDataDir } from "@/lib/paths";
import { db, dbInitError, dbMigrationError } from "@/lib/db";
import { projects } from "@/lib/db/schema";
import { sql } from "drizzle-orm";
import { ffmpegBin, ffprobeBin } from "@/lib/ffmpeg-path";
import { buildRuntimeHealth, type RuntimeCheck } from "@/lib/runtime-health";

// MainPG sidecar 健康契约：MainPG 后端只在 service/schemaVersion/instanceId/status
// 和五个 checks 全部合规时才认为 sidecar ready。因此这里只输出稳定结果与稳定错误码，
// 绝不返回运行时绝对路径、原始异常或供应商响应正文（用户报障仍可截这一张图）。
//
// 媒体探针缓存：前端在 starting 阶段每 800ms、ready 阶段每 5s 轮询一次状态，
// 每次都 spawn ffmpeg/ffprobe 会持续吃掉 CPU，因此每个 Node 进程只探测一次。
let mediaProbePromises: { ffmpeg: Promise<RuntimeCheck>; ffprobe: Promise<RuntimeCheck> } | null = null;

function probeMediaBinary(binary: string, code: string): Promise<RuntimeCheck> {
  return new Promise((resolve) => {
    execFile(binary, ["-version"], { timeout: 2000 }, (error) => {
      resolve(error ? { status: "error", code } : { status: "ok" });
    });
  });
}

function mediaChecks(): { ffmpeg: Promise<RuntimeCheck>; ffprobe: Promise<RuntimeCheck> } {
  if (!mediaProbePromises) {
    mediaProbePromises = {
      ffmpeg: probeMediaBinary(ffmpegBin(), "FFMPEG_UNAVAILABLE"),
      ffprobe: probeMediaBinary(ffprobeBin(), "FFPROBE_UNAVAILABLE"),
    };
  }
  return mediaProbePromises;
}

export async function GET() {
  // 数据库连通性：真实执行一条查询（能同时暴露原生模块 ABI 问题与表缺失问题）
  let databaseCheck: RuntimeCheck;
  if (dbInitError) {
    databaseCheck = { status: "error", code: "DB_INIT_FAILED" };
  } else {
    try {
      db.select({ n: sql<number>`count(*)` }).from(projects).get();
      databaseCheck = { status: "ok" };
    } catch {
      databaseCheck = { status: "error", code: "DB_QUERY_FAILED" };
    }
  }

  const migrationsCheck: RuntimeCheck = dbMigrationError
    ? { status: "error", code: "MIGRATIONS_FAILED" }
    : { status: "ok" };

  let dataDirCheck: RuntimeCheck;
  try {
    fs.accessSync(getDataDir(), fs.constants.W_OK);
    dataDirCheck = { status: "ok" };
  } catch {
    dataDirCheck = { status: "error", code: "DATA_DIR_NOT_WRITABLE" };
  }

  const media = mediaChecks();

  const payload = buildRuntimeHealth({
    instanceId: process.env.MAINPG_CLIPFORGE_INSTANCE_ID || "standalone",
    database: databaseCheck,
    migrations: migrationsCheck,
    dataDirWritable: dataDirCheck,
    ffmpeg: await media.ffmpeg,
    ffprobe: await media.ffprobe,
  });
  return NextResponse.json(payload, { status: payload.status === "ok" ? 200 : 503 });
}
