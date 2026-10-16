import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
const source = readFileSync(new URL("./podFissionApi.ts", import.meta.url), "utf8");
test("fission upload posts multipart role and exposes safe endpoints",()=>{assert.match(source,/form\.append\("file", file\)/); assert.match(source,/form\.append\("role", role\)/); assert.match(source,/source_artwork|target_product/); assert.match(source,/analysis_id/); assert.match(source,/retryStyle/);});
test("fission response types do not expose candidate pool fields",()=>{const types=readFileSync(new URL("../types.ts",import.meta.url),"utf8"); assert.doesNotMatch(types,/candidate_capabilities|unknown_capabilit|rules_response/); assert.match(types,/FissionAnalysisResponse/);});
