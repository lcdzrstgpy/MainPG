import assert from "node:assert/strict";
import test from "node:test";

import { buildSrt, buildVtt } from "../subtitle-export.ts";
import {
  DEFAULT_TRANSCRIPT_EDIT_PLAN,
  detectFillerWordIds,
  keepRangesForPlan,
  sanitizeTranscriptDocument,
  transcriptWordsToCues,
} from "../transcript-editor.ts";

const document = sanitizeTranscriptDocument(
  {
    version: 1,
    text: "嗯 我们开始 今天测试",
    language: "zh",
    duration: 6,
    model: "whisper-tiny",
    device: "wasm",
    words: [
      { id: "w1", text: "嗯", start: 0, end: 0.4 },
      { id: "w2", text: "我们", start: 0.6, end: 1.2 },
      { id: "w3", text: "开始", start: 1.2, end: 1.8 },
      { id: "w4", text: "今天测试", start: 2.4, end: 3.4 },
    ],
    segments: [],
    silenceRanges: [],
    createdAt: "2026-10-20T00:00:00.000Z",
  },
  6,
);

test("逐字稿文档保留词级时间戳", () => {
  assert.ok(document);
  assert.equal(document.words.length, 4);
  assert.equal(document.words[0].start, 0);
  assert.equal(document.words[3].end, 3.4);
});

test("语气词可按词删除，导出字幕不含被删内容", () => {
  assert.ok(document);
  assert.deepEqual(detectFillerWordIds(document), ["w1"]);
  const plan = { ...DEFAULT_TRANSCRIPT_EDIT_PLAN, removedWordIds: ["w1"] };
  const kept = keepRangesForPlan(document, plan);
  const cues = transcriptWordsToCues(document, kept, plan);
  const srt = buildSrt(cues);
  const vtt = buildVtt(cues);
  assert.match(srt, /-->/);
  assert.doesNotMatch(srt, /嗯/);
  assert.match(srt, /我们/);
  assert.match(vtt, /^WEBVTT/);
});

test("删除静音后保留区间会跳过静音段", () => {
  assert.ok(document);
  const plan = { ...DEFAULT_TRANSCRIPT_EDIT_PLAN, removeSilence: true };
  const kept = keepRangesForPlan(document, plan);
  assert.ok(kept.length > 0);
  assert.ok(kept[0].start >= 0);
  assert.ok(kept[kept.length - 1].end <= document.duration + 0.001);
});
