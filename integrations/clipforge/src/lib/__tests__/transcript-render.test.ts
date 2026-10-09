import { execFile } from "child_process";
import { mkdtemp, rm, access } from "fs/promises";
import { tmpdir } from "os";
import { join } from "path";
import { promisify } from "util";
import { afterEach, describe, expect, it } from "vitest";
import { ffmpegBin } from "@/lib/ffmpeg-path";
import { probeMedia } from "@/lib/media-probe";
import { buildTranscriptRenderInvocation, renderTranscriptEdit } from "@/lib/transcript-render";
import { DEFAULT_TRANSCRIPT_EDIT_PLAN, keepRangesForPlan, transcriptWordsToCues, type TranscriptDocument } from "@/lib/transcript-editor";

const execFileAsync = promisify(execFile);
const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
});
describe("transcript render invocation", () => {
  it("keeps video and audio pairs ordered through concat", () => {
    const invocation = buildTranscriptRenderInvocation({
      inputPath: "/source.mp4",
      outputPath: "/output.mp4",
      keepRanges: [{ start: 0, end: 1 }, { start: 2, end: 4 }],
      hasAudio: true,
      subtitlePath: "/tmp/caption.ass",
      fontDirectory: "/tmp/fonts",
      duration: 3,
    });
    expect(invocation.filterComplex).toContain("[v0][a0][v1][a1]concat=n=2:v=1:a=1[vcat][acat]");
    expect(invocation.filterComplex).toContain("[vbase]subtitles=/tmp/caption.ass:fontsdir=/tmp/fonts[vout]");
    expect(invocation.outputArgs).toContain("[acat]");
    expect(invocation.outputArgs.at(-1)).toBe("/output.mp4");
  });

  it("does not map an audio stream for silent sources", () => {
    const invocation = buildTranscriptRenderInvocation({
      inputPath: "/source.mp4",
      outputPath: "/output.mp4",
      keepRanges: [{ start: 0, end: 1 }],
      hasAudio: false,
      duration: 1,
    });
    expect(invocation.filterComplex).not.toContain("atrim");
    expect(invocation.outputArgs).not.toContain("-c:a");
  });
});

describe("transcript renderer integration", () => {
  it("creates a valid non-destructive cut with synchronized audio", async () => {
    const directory = await mkdtemp(join(tmpdir(), "clipforge-text-edit-"));
    temporaryDirectories.push(directory);
    const inputPath = join(directory, "source.mp4");
    const outputPath = join(directory, "edited.mp4");
    await execFileAsync(ffmpegBin(), [
      "-nostdin", "-v", "error", "-y",
      "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=3",
      "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=3",
      "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", inputPath,
    ]);
    const transcript: TranscriptDocument = {
      version: 1,
      text: "one two",
      language: "en",
      duration: 3,
      model: "test",
      device: "wasm",
      words: [{ id: "w1", text: "one", start: 0.2, end: 0.7 }, { id: "w2", text: "two", start: 2.2, end: 2.7 }],
      segments: [], silenceRanges: [], createdAt: "2026-08-25T00:00:00.000Z",
    };
    await renderTranscriptEdit({
      projectId: "test-render",
      sourcePath: inputPath,
      sourceWidth: 320,
      sourceHeight: 180,
      hasAudio: true,
      transcript,
      plan: { ...DEFAULT_TRANSCRIPT_EDIT_PLAN, burnSubtitles: false },
      keepRanges: [{ start: 0, end: 1 }, { start: 2, end: 3 }],
      outputPath,
    });
    const probe = await probeMedia(outputPath);
    expect(probe.width).toBe(320);
    expect(probe.height).toBe(180);
    expect(probe.hasAudio).toBe(true);
    expect(probe.duration).toBeGreaterThan(1.85);
    expect(probe.duration).toBeLessThan(2.15);

    const clipPlan = { ...DEFAULT_TRANSCRIPT_EDIT_PLAN, sourceRange: { start: 2.1, end: 2.9 } };
    const keepRanges = keepRangesForPlan(transcript, clipPlan);
    const cues = transcriptWordsToCues(transcript, keepRanges);
    expect(cues).toHaveLength(1);
    expect(cues[0].text).toBe("two");
    expect(cues[0].startMs).toBeCloseTo(100);
    const clipPath = join(directory, "clip.mp4");
    await renderTranscriptEdit({
      projectId: "test-render", sourcePath: inputPath, sourceWidth: 320, sourceHeight: 180,
      hasAudio: true, transcript, plan: clipPlan, keepRanges, outputPath: clipPath,
    });
    const clipProbe = await probeMedia(clipPath);
    expect(clipProbe.hasAudio).toBe(true);
    expect(clipProbe.duration).toBeGreaterThan(0.7);
    expect(clipProbe.duration).toBeLessThan(0.95);
    expect((await probeMedia(inputPath)).duration).toBeCloseTo(3, 1);

    // The optimized seek must preserve the same first/last frames as decoding from zero.
    const baseline = join(directory, "baseline.mp4");
    const optimized = join(directory, "optimized.mp4");
    for (const [path, seekInput] of [[baseline, false], [optimized, true]] as const) {
      const invocation = buildTranscriptRenderInvocation({ inputPath, outputPath: path, keepRanges, hasAudio: true, duration: 0.8, seekInput });
      await execFileAsync(ffmpegBin(), [...invocation.inputArgs, "-filter_complex", invocation.filterComplex, ...invocation.outputArgs]);
    }
    const compared = await execFileAsync(ffmpegBin(), ["-v", "info", "-i", baseline, "-i", optimized, "-lavfi", "[0:v][1:v]ssim", "-f", "null", "-"]);
    expect(Number(/All:([\d.]+)/.exec(compared.stderr)?.[1])).toBeGreaterThan(0.99);
    const audio = async (path: string) => (await execFileAsync(ffmpegBin(), ["-v", "error", "-i", path, "-vn", "-ar", "16000", "-f", "f32le", "pipe:1"], { encoding: "buffer" })).stdout;
    const [a, b] = await Promise.all([audio(baseline), audio(optimized)]);
    expect(Math.abs(a.length - b.length)).toBeLessThan(16000 * 4 * 0.04);
    let difference = 0;
    for (let offset = 0; offset < Math.min(a.length, b.length); offset += 4) difference += Math.abs(a.readFloatLE(offset) - b.readFloatLE(offset));
    expect(difference / (Math.min(a.length, b.length) / 4)).toBeLessThan(0.02);

    const cancelledPath = join(directory, "cancelled.mp4");
    const controller = new AbortController();
    await expect(renderTranscriptEdit({ projectId: "test-render", sourcePath: inputPath, sourceWidth: 320, sourceHeight: 180, hasAudio: true, transcript, plan: { ...DEFAULT_TRANSCRIPT_EDIT_PLAN, burnSubtitles: false }, keepRanges, outputPath: cancelledPath, signal: controller.signal, onStart: () => controller.abort(new Error("cancel-test")) })).rejects.toThrow("cancel-test");
    await expect(access(cancelledPath)).rejects.toThrow();
  }, 30_000);
});
