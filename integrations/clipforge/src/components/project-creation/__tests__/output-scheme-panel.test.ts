import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OutputSchemePanel } from "@/components/project-creation/output-scheme-panel";
import { OUTPUT_SCHEMES, type OutputSchemeSnapshot } from "@/lib/output-schemes";

const roots: Array<() => Promise<void>> = [];

async function renderPanel(value: OutputSchemeSnapshot, onChange = vi.fn()) {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => root.render(createElement(OutputSchemePanel, { value, onChange })));
  roots.push(async () => {
    await act(async () => root.unmount());
    container.remove();
  });
  return { container, onChange };
}

afterEach(async () => {
  for (const cleanup of roots.splice(0)) await cleanup();
  vi.unstubAllGlobals();
});

describe("OutputSchemePanel", () => {
  it("renders exactly five controlled scheme cards and describes each production chain", async () => {
    const { container } = await renderPanel({ ...OUTPUT_SCHEMES["native-film"] });
    const cards = [...container.querySelectorAll<HTMLButtonElement>("[data-scheme]")];
    expect(cards.map((card) => card.dataset.scheme)).toEqual([
      "draft", "controlled-rapid", "controlled-balanced", "controlled-cinematic", "native-film",
    ]);
    expect(cards.filter((card) => card.getAttribute("aria-checked") === "true").map((card) => card.dataset.scheme)).toEqual(["native-film"]);
    expect(container.textContent).toContain("静态素材");
    expect(container.textContent).toContain("FFmpeg");
    expect(container.textContent).toContain("逐镜关键帧");
    expect(container.textContent).toContain("逐镜 I2V");
    expect(container.textContent).toContain("九宫格参考图");
    expect(container.textContent).toContain("一次整片视频模型调用");
  });

  it("emits the chosen preset as a snapshot with its default audio", async () => {
    const { container, onChange } = await renderPanel({ ...OUTPUT_SCHEMES["native-film"] });
    await act(async () => container.querySelector<HTMLButtonElement>('[data-scheme="controlled-cinematic"]')!.click());
    expect(onChange).toHaveBeenCalledWith({ ...OUTPUT_SCHEMES["controlled-cinematic"] });
  });

  it("locks native film to model-native audio", async () => {
    const { container } = await renderPanel({ ...OUTPUT_SCHEMES["native-film"] });
    expect(container.textContent).toContain("模型原生音频");
    expect(container.textContent).not.toContain("火山语音配音（TTS）");
    expect(container.querySelectorAll("[data-audio-strategy]")).toHaveLength(0);
  });

  it("offers TTS and mute for draft and director schemes", async () => {
    const { container, onChange } = await renderPanel({ ...OUTPUT_SCHEMES["controlled-balanced"] });
    expect([...container.querySelectorAll<HTMLButtonElement>("[data-audio-strategy]")].map((button) => button.dataset.audioStrategy)).toEqual(["volcengine-tts", "mute"]);
    await act(async () => container.querySelector<HTMLButtonElement>('[data-audio-strategy="mute"]')!.click());
    expect(onChange).toHaveBeenCalledWith({ ...OUTPUT_SCHEMES["controlled-balanced"], audioStrategy: "mute" });
  });
});
