import { describe, expect, it } from "vitest";

import { enabledMainPgMediaProviders } from "../gen-params";

describe("MainPG media provider boundary", () => {
  it("does not forward legacy persisted providers to image/video routes", () => {
    expect(enabledMainPgMediaProviders({
      volcengine: { enabled: true, apiKey: "ark-key" },
      suchuang: { enabled: true, apiKey: "speed-key" },
      "atlas-cloud": { enabled: true, apiKey: "legacy-key" },
      replicate: { enabled: true, apiKey: "legacy-key" },
    })).toEqual([
      { name: "volcengine", apiKey: "ark-key", baseUrl: undefined },
      { name: "suchuang", apiKey: "speed-key", baseUrl: undefined },
    ]);
  });
});
