import { describe, expect, it } from "vitest";
import { buildRuntimeHealth } from "@/lib/runtime-health";

const healthy = {
  instanceId: "instance-a",
  database: { status: "ok" as const },
  migrations: { status: "ok" as const },
  dataDirWritable: { status: "ok" as const },
  ffmpeg: { status: "ok" as const },
  ffprobe: { status: "ok" as const },
};

describe("MainPG runtime health contract", () => {
  it("reports ok only when every critical check is ok", () => {
    expect(buildRuntimeHealth(healthy)).toEqual({
      service: "clipforge",
      schemaVersion: 1,
      instanceId: "instance-a",
      status: "ok",
      checks: {
        database: { status: "ok" },
        migrations: { status: "ok" },
        dataDirWritable: { status: "ok" },
        ffmpeg: { status: "ok" },
        ffprobe: { status: "ok" },
      },
    });
  });

  it("reports error and a stable code without absolute paths", () => {
    const payload = buildRuntimeHealth({
      ...healthy,
      database: { status: "error" as const, code: "DB_UNAVAILABLE" },
    });
    expect(payload.status).toBe("error");
    expect(payload.checks.database).toEqual({ status: "error", code: "DB_UNAVAILABLE" });
    expect(JSON.stringify(payload)).not.toContain("/Users/");
    expect(JSON.stringify(payload)).not.toContain("C:\\");
  });

  it("turns error when any single required check fails", () => {
    const payload = buildRuntimeHealth({
      ...healthy,
      ffprobe: { status: "error" as const, code: "FFPROBE_UNAVAILABLE" },
    });
    expect(payload.status).toBe("error");
    expect(payload.checks.ffprobe).toEqual({ status: "error", code: "FFPROBE_UNAVAILABLE" });
  });
});
