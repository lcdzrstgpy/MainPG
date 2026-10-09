import { describe, expect, it } from "vitest";
import { suggestTranscriptClips } from "@/lib/transcript-clips";
import { DEFAULT_TRANSCRIPT_EDIT_PLAN, keepRangesForPlan, outputDuration, sanitizeTranscriptEditPlan, transcriptWordsToCues, type TranscriptDocument } from "@/lib/transcript-editor";
import { createTranscriptEditProposal } from "@/lib/transcript-edit-protocol";
import { exportTimeline } from "@/lib/timeline-export";

function fixture(language = "zh"): TranscriptDocument {
  const phrases = language === "zh"
    ? ["今天试试这个杯子。", "它放进包里也不漏水。", "再来看看保温效果。", "三小时后还是温热的。", "最后清洗一下就好了。", "这就是今天的使用体验。"]
    : ["Here is the Travel Mug.", "It fits inside my bag.", "The water stays warm.", "After three hours it is still warm.", "Cleaning takes a few seconds.", "That is my daily routine."];
  return { version: 1, language, text: phrases.join(""), duration: 60, model: "test", device: "wasm", createdAt: "2026-09-05T00:00:00Z",
    words: phrases.flatMap((phrase, i) => (language === "zh" ? [...phrase] : phrase.split(" ")).map((text, j, all) => ({
      id: `w-${i}-${j}`, text, start: 10 + i * 5 + j * 4 / all.length, end: 10 + i * 5 + (j + 1) * 4 / all.length,
    }))), segments: [], silenceRanges: [{ start: 0, end: 10 }, { start: 39, end: 60 }],
  };
}

describe("local transcript clips", () => {
  it("finds Chinese phrases across word tokens with actual evidence and source times", () => {
    const document = fixture();
    const result = suggestTranscriptClips(document, { query: "不漏水", targetSeconds: 15 });
    expect(result.candidates.length).toBeGreaterThan(0);
    for (const clip of result.candidates) {
      expect(clip.text).toContain("不漏水");
      expect(clip.reasons).toContain("query");
      expect(clip.sourceRange.start).toBeGreaterThan(9);
      expect(clip.sourceRange.end).toBeLessThan(40);
      expect(outputDuration(keepRangesForPlan(document, clip.plan))).toBeCloseTo(clip.duration);
      expect(clip.duration).toBeGreaterThan(10);
      expect(clip.duration).toBeLessThan(20);
    }
  });

  it("matches English phrases case-insensitively and handles full-width text", () => {
    expect(suggestTranscriptClips(fixture("en"), { query: "ＴＲＡＶＥＬ  mug" }).candidates.length).toBeGreaterThan(0);
    expect(suggestTranscriptClips(fixture(), { query: "不存在的产品" }).candidates).toEqual([]);
  });

  it("returns bounded, diverse ranges without mutating the transcript", () => {
    const document = fixture();
    const before = JSON.stringify(document);
    const clips = suggestTranscriptClips(document, { targetSeconds: 5, limit: 3 }).candidates;
    expect(clips).toHaveLength(3);
    expect(JSON.stringify(document)).toBe(before);
    for (let i = 1; i < clips.length; i++) expect(clips[i].sourceRange.start).toBeGreaterThanOrEqual(clips[i - 1].sourceRange.end);
    expect(suggestTranscriptClips({ ...document, words: [] }).candidates).toEqual([]);
  });

  it("does not join speech across a long silent stretch", () => {
    const document = fixture();
    document.words = document.words.map((word) => word.start >= 25 ? { ...word, start: word.start + 10, end: word.end + 10 } : word);
    for (const clip of suggestTranscriptClips(document, { targetSeconds: 60 }).candidates) {
      expect(clip.sourceRange.start >= 34 || clip.sourceRange.end <= 25).toBe(true);
    }
  });
});

describe("source range editing contract", () => {
  const document = fixture();
  const wordIds = new Set(document.words.map((word) => word.id));

  it("removes all leading and trailing silence even with silence removal off", () => {
    const plan = { ...DEFAULT_TRANSCRIPT_EDIT_PLAN, sourceRange: { start: 15, end: 19 } };
    expect(keepRangesForPlan(document, plan)).toEqual([{ start: 15, end: 19 }]);
    const cues = transcriptWordsToCues(document, keepRangesForPlan(document, plan));
    expect(cues.map((cue) => cue.text).join("")).toBe("它放进包里也不漏水。");
    expect(cues[0].startMs).toBeCloseTo(0);
    expect(cues.at(-1)!.endMs).toBeLessThanOrEqual(4000);
    const timeline = exportTimeline("csv", { projectName: "cut", sourceName: "source.mp4", sourceDuration: 60, frameRate: 30, hasAudio: true, keepRanges: keepRangesForPlan(document, plan) });
    expect(timeline.duration).toBe(4);
  });

  it("combines a crop with word removal and preserves sub-frame crop edges", () => {
    const plan = { ...DEFAULT_TRANSCRIPT_EDIT_PLAN, sourceRange: { start: 15, end: 19 }, removedWordIds: ["w-1-3"] };
    const ranges = keepRangesForPlan(document, plan);
    expect(outputDuration(ranges)).toBeLessThan(4);
    expect(ranges.every((range) => range.start >= 15 && range.end <= 19)).toBe(true);
    expect(keepRangesForPlan(document, { ...DEFAULT_TRANSCRIPT_EDIT_PLAN, sourceRange: { start: 0.01, end: 59.99 } })).toEqual([{ start: 0.01, end: 59.99 }]);
  });

  it.each([null, {}, { start: -1, end: 3 }, { start: 1, end: 61 }, { start: 4, end: 2 }, { start: NaN, end: 3 }, { start: 1, end: Infinity }, { start: "1", end: 3 }])("rejects invalid ranges instead of silently exporting the entire source: %j", (sourceRange) => {
    expect(() => sanitizeTranscriptEditPlan({ ...DEFAULT_TRANSCRIPT_EDIT_PLAN, sourceRange }, wordIds, document.duration)).toThrow("INVALID_TRANSCRIPT_SOURCE_RANGE");
  });

  it("keeps older plans compatible and includes crop/padding edits in revision diffs", () => {
    expect(sanitizeTranscriptEditPlan(DEFAULT_TRANSCRIPT_EDIT_PLAN, wordIds, 60)).toEqual(DEFAULT_TRANSCRIPT_EDIT_PLAN);
    for (const plan of [
      { ...DEFAULT_TRANSCRIPT_EDIT_PLAN, sourceRange: { start: 15, end: 19 } },
      { ...DEFAULT_TRANSCRIPT_EDIT_PLAN, wordPaddingMs: 100 },
    ]) {
      const proposal = createTranscriptEditProposal({ document, value: { plan, baseRevision: 2 }, latestRevision: 3, fallbackOperationId: "test-clip-op" });
      expect(proposal.changed).toBe(true);
      expect(proposal.conflict).toBe(true);
      if (plan.sourceRange) {
        expect(proposal.diff.sourceRangeChanged).toBe(true);
        expect(proposal.summary.removedWordCount).toBeGreaterThan(0);
        expect(proposal.summary.outputDuration).toBe(4);
      } else expect(proposal.diff.paddingChanged).toBe(true);
    }
  });
});
