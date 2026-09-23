import { describe, expect, it } from "vitest";
import { DEFAULT_BGM_VOLUME, normalizeBgmVolume, resolveBgmMix } from "@/lib/audio-mix";

describe("audio mix policy", () => {
  it("clamps unsafe BGM levels and keeps a stable default", () => {
    expect(normalizeBgmVolume(undefined)).toBe(DEFAULT_BGM_VOLUME);
    expect(normalizeBgmVolume("not-a-number")).toBe(DEFAULT_BGM_VOLUME);
    expect(normalizeBgmVolume(0)).toBe(0.05);
    expect(normalizeBgmVolume(0.99)).toBe(0.4);
    expect(normalizeBgmVolume(0.237)).toBe(0.24);
  });

  it("enables ducking by default only when a voice track exists", () => {
    expect(resolveBgmMix({ hasVoice: true })).toEqual({ volume: 0.18, duck: true });
    expect(resolveBgmMix({ hasVoice: true, duck: false, volume: 0.25 })).toEqual({ volume: 0.25, duck: false });
    expect(resolveBgmMix({ hasVoice: false, duck: true })).toEqual({ volume: 0.18, duck: false });
  });
});
