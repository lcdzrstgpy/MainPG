import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("focus recovery layout effect is registered before the dialog can return null", async () => {
  const source = await readFile(new URL("./AppUpdateDialog.tsx", import.meta.url), "utf8");
  const hiddenReturnIndex = source.indexOf("if (!isVisible) return null;");
  const lastLayoutEffectIndex = source.lastIndexOf("useLayoutEffect(() =>");

  assert.notEqual(hiddenReturnIndex, -1, "expected the hidden dialog guard");
  assert.notEqual(lastLayoutEffectIndex, -1, "expected the focus recovery layout effect");
  assert.ok(
    lastLayoutEffectIndex < hiddenReturnIndex,
    "all layout effects must be registered before the hidden dialog guard to preserve hook ordering",
  );
});
