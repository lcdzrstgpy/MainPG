import assert from "node:assert/strict";
import test from "node:test";

import { aiVideoMediaUrl } from "../media-url.ts";

test("相对媒体路径统一拼到同源代理", () => {
  assert.equal(aiVideoMediaUrl("/api/files/p/a.png"), "/api/clipforge/proxy/api/files/p/a.png");
  assert.equal(aiVideoMediaUrl("api/output/p/a.mp4"), "/api/clipforge/proxy/api/output/p/a.mp4");
});

test("绝对地址、data 与 blob 原样保留；空值返回 null", () => {
  assert.equal(aiVideoMediaUrl("https://example.com/a.png"), "https://example.com/a.png");
  assert.equal(aiVideoMediaUrl("data:image/png;base64,AAA"), "data:image/png;base64,AAA");
  assert.equal(aiVideoMediaUrl("blob:http://localhost/x"), "blob:http://localhost/x");
  assert.equal(aiVideoMediaUrl(null), null);
  assert.equal(aiVideoMediaUrl(""), null);
});
