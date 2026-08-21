import assert from "node:assert/strict";
import test from "node:test";

import { podStyleTitleRegenerateRequest } from "../data/styleTitleRequest.ts";

test("title retry uses the isolated POD endpoint", () => {
  assert.deepEqual(podStyleTitleRegenerateRequest("batch / 1", 12), {
    path: "/api/pod-customization/batches/batch%20%2F%201/styles/12/title/regenerate",
    options: { method: "POST", body: {} },
  });
});
