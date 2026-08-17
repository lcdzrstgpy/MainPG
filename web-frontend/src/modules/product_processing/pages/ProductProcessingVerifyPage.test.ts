import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("./ProductProcessingVerifyPage.tsx", import.meta.url), "utf8");

test("draft toolbar sticky behavior does not observe its own layout writes", () => {
  assert.match(page, /const \[isStickyToolbar, setIsStickyToolbar\] = useState\(false\)/);
  assert.match(page, /const stickyToolbarStateRef = useRef\(false\)/);
  assert.match(page, /if \(!isActive\) \{[\s\S]*?clearStickyLayout\(\);[\s\S]*?return;/);
  assert.match(page, /if \(!stickyToolbarStateRef\.current\) \{[\s\S]*?setIsStickyToolbar\(true\)/);
  assert.match(page, /\}, \[isActive\]\);/);
  assert.doesNotMatch(page, /observer\.observe\(toolbar\)/);
  assert.doesNotMatch(page, /observer\.observe\(document\.body\)/);
});
