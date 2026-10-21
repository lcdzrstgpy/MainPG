import assert from "node:assert/strict";
import test from "node:test";

import {
  enabledMainPgMediaProviders,
  imageSize,
  mergeCustomModels,
  videoSize,
  type CustomModel,
} from "../gen-params.ts";

test("imageSize maps aspect ratios to portrait-first dimensions", () => {
  assert.deepEqual(imageSize("9:16"), { width: 1080, height: 1920 });
  assert.deepEqual(imageSize("16:9"), { width: 1920, height: 1080 });
  assert.deepEqual(imageSize("1:1"), { width: 1024, height: 1024 });
});

test("videoSize combines resolution tier with aspect ratio", () => {
  assert.deepEqual(videoSize("1080p", "9:16"), { width: 1080, height: 1920 });
  assert.deepEqual(videoSize("1080p", "16:9"), { width: 1920, height: 1080 });
  assert.deepEqual(videoSize("1080p", "1:1"), { width: 1080, height: 1080 });
  assert.deepEqual(videoSize("720p", "9:16"), { width: 720, height: 1280 });
  assert.deepEqual(videoSize("720p", "16:9"), { width: 1280, height: 720 });
  assert.deepEqual(videoSize("720p", "1:1"), { width: 720, height: 720 });
});

test("mergeCustomModels keeps only matching media type and enabled providers", () => {
  const fetched = [{ id: "official-image", name: "Official", provider: "volcengine" }];
  const custom: CustomModel[] = [
    { id: "c1", provider: "volcengine", modelId: "my-image", name: "My Image", mediaType: "image" },
    { id: "c2", provider: "volcengine", modelId: "my-video", name: "My Video", mediaType: "video" },
    { id: "c3", provider: "disabled", modelId: "orphan", name: "Orphan", mediaType: "image" },
    { id: "c4", provider: "volcengine", modelId: "official-image", name: "Duplicate", mediaType: "image" },
  ];

  const imageModels = mergeCustomModels(fetched, custom, "image", new Set(["volcengine"]));
  assert.deepEqual(
    imageModels.map((model) => model.id),
    ["official-image", "my-image"]
  );
  assert.equal(imageModels[1].custom, true);
  assert.deepEqual(imageModels[1].modes, ["text-to-image", "image-to-image"]);

  const videoModels = mergeCustomModels(fetched, custom, "video", new Set(["volcengine"]));
  assert.deepEqual(
    videoModels.map((model) => model.id),
    ["official-image", "my-video"]
  );
});

test("mergeCustomModels tolerates a missing custom list and disabled provider list", () => {
  const fetched = [{ id: "official", name: "Official", provider: "volcengine" }];
  assert.deepEqual(mergeCustomModels(fetched, undefined, "image").map((model) => model.id), ["official"]);

  const custom: CustomModel[] = [
    { id: "c1", provider: "missing", modelId: "custom", name: "Custom", mediaType: "image" },
  ];
  assert.deepEqual(
    mergeCustomModels(fetched, custom, "image", new Set(["volcengine"])).map((model) => model.id),
    ["official"]
  );
});

test("enabledMainPgMediaProviders only forwards enabled volcengine / suchuang entries with keys", () => {
  const providers = {
    volcengine: { enabled: true, apiKey: "k1", baseUrl: "https://a" },
    suchuang: { enabled: true, apiKey: "k2" },
    "fal-ai": { enabled: true, apiKey: "legacy" },
    replicate: { enabled: true, apiKey: "legacy" },
    openai: { enabled: false, apiKey: "k3" },
  };
  assert.deepEqual(enabledMainPgMediaProviders(providers), [
    { name: "volcengine", apiKey: "k1", baseUrl: "https://a" },
    { name: "suchuang", apiKey: "k2", baseUrl: undefined },
  ]);
});
