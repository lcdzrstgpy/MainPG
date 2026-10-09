// @vitest-environment node
import { mkdtemp, rm, writeFile } from "fs/promises";
import { join } from "path";
import { tmpdir } from "os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { fileResponseStream } from "@/lib/file-response-stream";
let directory: string;
let path: string;
const bytes = Buffer.alloc(2 * 1024 * 1024);
for (let index = 0; index < bytes.length; index++) bytes[index] = index % 251;
beforeEach(async () => { directory = await mkdtemp(join(tmpdir(), "clipforge-stream-")); path = join(directory, "video.bin"); await writeFile(path, bytes); });
afterEach(async () => { await rm(directory, { recursive: true, force: true }); });
describe("cancel-safe file responses", () => {
  it("preserves full and range byte contents", async () => {
    // Compare every byte natively; recursive object equality is costly for multi-MB buffers in CI.
    expect(Buffer.from(await new Response(fileResponseStream(path)).arrayBuffer()).equals(bytes)).toBe(true);
    expect(Buffer.from(await new Response(fileResponseStream(path, { start: 10, end: 999 })).arrayBuffer()).equals(bytes.subarray(10, 1000))).toBe(true);
  });
  it("handles repeated seek-style cancellations before and during reads", async () => {
    for (let index = 0; index < 30; index++) {
      const reader = fileResponseStream(path, { start: index * 1000, end: bytes.length - 1 }).getReader();
      if (index % 2) expect((await reader.read()).value?.length).toBeGreaterThan(0);
      else void reader.read();
      await reader.cancel();
      expect((await reader.read()).done).toBe(true);
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  });
  it("reports a read error once and tolerates cancellation during an open error", async () => {
    await expect(new Response(fileResponseStream(join(directory, "missing"))).arrayBuffer()).rejects.toThrow();
    await fileResponseStream(join(directory, "also-missing")).cancel();
    await new Promise((resolve) => setTimeout(resolve, 10));
  });
});
