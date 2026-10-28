import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
const source = readFileSync(new URL("./podFissionApi.ts", import.meta.url), "utf8");
test("fission upload posts multipart role and exposes safe endpoints",()=>{assert.match(source,/form\.append\("file", file\)/); assert.match(source,/form\.append\("role", role\)/); assert.match(source,/source_artwork|target_product/); assert.match(source,/retryStyle/);});
test("fission has no standalone analyze endpoint",()=>{assert.doesNotMatch(source,/analyzeAssets/); assert.doesNotMatch(source,/\/analyze/); assert.match(source,/createBatch/);});
test("fission response types do not expose candidate pools or analysis summaries",()=>{const types=readFileSync(new URL("../types.ts",import.meta.url),"utf8"); assert.doesNotMatch(types,/candidate_capabilities|unknown_capabilit|rules_response/); assert.doesNotMatch(types,/FissionAnalysisResponse|analysis_id/); assert.match(types,/CreateFissionBatchRequest/); assert.match(types,/source_asset_id/); assert.match(types,/target_asset_id/);});
