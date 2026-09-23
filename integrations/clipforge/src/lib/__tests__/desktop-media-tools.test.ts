// @vitest-environment node
import { mkdtempSync, mkdirSync, writeFileSync, statSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { createRequire } from "node:module";
import { beforeEach, afterEach, describe, expect, it } from "vitest";

const { prepareBundledMediaTools } = createRequire(import.meta.url)("../../../electron/media-tools.cjs");
let directory: string;
beforeEach(() => { directory = mkdtempSync(join(tmpdir(), "clipforge-desktop-tools-")); });
afterEach(() => rmSync(directory, { recursive: true, force: true }));
function tools(script = "#!/bin/sh\necho fixture version\n") {
  const modules = join(directory, "app.asar.unpacked", "node_modules");
  const paths = [join(modules, "ffmpeg-static", "ffmpeg"), join(modules, "@ffprobe-installer", `${process.platform}-${process.arch}`, "ffprobe")];
  for (const file of paths) {
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, script, { mode: 0o644 });
  }
  return paths;
}

describe("桌面包媒体工具验收", () => {
  it.skipIf(process.platform === "win32")("修复没有执行权限的工具，并在最终打包目录实际运行", () => {
    const paths = tools();
    expect(prepareBundledMediaTools(directory, process.platform, process.arch)).toEqual(paths);
    for (const file of paths) expect(statSync(file).mode & 0o777).toBe(0o755);
  });
  it("缺少工具时阻止生成安装包", () => {
    expect(() => prepareBundledMediaTools(directory, "darwin", "arm64")).toThrow("缺少媒体工具");
  });
  it.skipIf(process.platform === "win32")("工具无法运行时阻止出包，而不是只检查文件存在", () => {
    tools("#!/bin/sh\nexit 1\n");
    expect(() => prepareBundledMediaTools(directory, process.platform, process.arch)).toThrow();
  });
});
