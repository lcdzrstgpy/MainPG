# 火山语音 V3 配音 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken Ark/OpenAI-compatible voice preset with a tested Volcengine Speech V3 HTTP Chunked adapter.

**Architecture:** `tts-presets.ts` supplies one visible Volcengine V3 configuration. `tts.ts` sends the documented V3 headers/body and incrementally collects base64 audio from the HTTP stream. The settings page exposes only fields needed by V3 and reports the actual preview error.

**Tech Stack:** Next.js 16, TypeScript, Vitest, native fetch/ReadableStream.

## Global Constraints

- Use `https://openspeech.bytedance.com/api/v3/tts/unidirectional` and V3 request headers.
- Do not echo, persist, or test with any user-provided secret.
- Leave image/video provider routing untouched.

---

### Task 1: Add V3 stream parser and HTTP adapter

**Files:**
- Modify: `integrations/clipforge/src/lib/tts.ts`
- Test: `integrations/clipforge/src/lib/__tests__/volcengine-tts-v3.test.ts`

**Interfaces:**
- Produces: `parseVolcengineTTSStream(chunks: AsyncIterable<Uint8Array>): Promise<Buffer>`
- Consumes: `TTSConfig` extended with `resourceId?: string` and `speechRate?: number`

- [ ] **Step 1: Write the failing test**

```ts
expect(await parseVolcengineTTSStream(chunksOf('data: {"code":0,"data":"YQ=="}\n\ndata: {"code":20000000}\n\n'))).toEqual(Buffer.from('a'));
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm exec vitest run src/lib/__tests__/volcengine-tts-v3.test.ts`

- [ ] **Step 3: Implement the minimal V3 parser and adapter**

Build JSON request body with `speaker`, `model`, and `audio_params`; post with `X-Api-Key`, `X-Api-Resource-Id`, `X-Api-Request-Id`; collect MP3 chunks and reject nonzero business codes.

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm exec vitest run src/lib/__tests__/volcengine-tts-v3.test.ts`

### Task 2: Expose only the V3 configuration in settings

**Files:**
- Modify: `integrations/clipforge/src/lib/tts-presets.ts`
- Modify: `integrations/clipforge/src/lib/stores/settings-store.ts`
- Modify: `integrations/clipforge/src/app/settings/page.tsx`
- Test: `integrations/clipforge/src/lib/__tests__/volcengine-tts-v3.test.ts`

**Interfaces:**
- Produces: resolved config containing `provider: "volcengine-speech"`, `resourceId`, and `speechRate`.

- [ ] **Step 1: Write the failing test**

```ts
expect(resolveTTSConfig({ enabled: true, provider: 'volcengine-speech', apiKey: 'key', voice: 'voice' }, {})).toMatchObject({ resourceId: 'seed-tts-2.0', model: 'seed-tts-2.0-expressive' });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm exec vitest run src/lib/__tests__/volcengine-tts-v3.test.ts`

- [ ] **Step 3: Implement the visible configuration**

Remove Atlas/MiniMax/fal.ai and the Ark quick preset from the selector; add V3 resource ID, engine, speaker and speech-rate fields.

- [ ] **Step 4: Run tests and build**

Run: `pnpm exec vitest run src/lib/__tests__/volcengine-tts-v3.test.ts src/lib/__tests__/tts.test.ts && pnpm build`
