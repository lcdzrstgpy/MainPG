import { describe, expect, it } from "vitest";
import { LOCAL_ASR_MODELS, isLocalAsrModel } from "@/lib/local-asr";
import { characterErrorRate, timestampQuality } from "../../../scripts/lib/asr-metrics.mjs";
describe("local ASR quality gate", () => {
  it("only offers word-timestamp-capable models for text editing", () => {
    expect(LOCAL_ASR_MODELS.map((model) => model.id)).toEqual(["onnx-community/whisper-tiny_timestamped", "onnx-community/whisper-base_timestamped", "onnx-community/whisper-small_timestamped"]);
    expect(isLocalAsrModel("onnx-community/whisper-tiny")).toBe(false);
  });
  it("counts insertions, deletions and substitutions without hiding number or script differences", () => {
    expect(characterErrorRate("ＡBC。", "abc").cer).toBe(0);
    expect(characterErrorRate("abc", "axcd")).toMatchObject({ errors: 2, characters: 3 });
    expect(characterErrorRate("九十九", "99").cer).toBeGreaterThan(0);
    expect(() => characterErrorRate("", "x")).toThrow();
  });
  it("flags empty, reversed, nonnumeric and out-of-source timestamps", () => {
    expect(timestampQuality([{ timestamp: [0, 1] }, { timestamp: [null, 2] }, { timestamp: [3, 2] }, { timestamp: [4, 8] }], 5).validRatio).toBe(0.25);
    expect(timestampQuality([], 5).validRatio).toBe(0);
  });
});
