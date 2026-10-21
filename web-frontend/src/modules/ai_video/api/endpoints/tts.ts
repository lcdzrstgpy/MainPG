import { cfBlob, type CfInit } from "../clipforgeClient";

/** POST /api/tts —— 付费 TTS 试听，返回 mp3 二进制。 */
export function synthesizePaidTts(
  text: string,
  ttsConfig: Record<string, unknown>,
  init?: CfInit,
) {
  return cfBlob("/api/tts", { ...init, method: "POST", json: { text, ttsConfig } });
}

/** POST /api/tts/free —— Edge 免费音色试听（无需 Key）。 */
export function synthesizeFreeTts(
  text: string,
  options: { voice?: string; rate?: string } = {},
  init?: CfInit,
) {
  return cfBlob("/api/tts/free", { ...init, method: "POST", json: { text, ...options } });
}
