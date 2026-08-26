import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./PodFailedRetryDialog.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles/podCustomization.css", import.meta.url), "utf8");

test("failed retry dialog groups image and title retry candidates with default selection", () => {
  assert.match(source, /图片失败（整款重生成）/);
  assert.match(source, /标题失败（仅重生标题）/);
  assert.match(source, /useState\(\(\) => allCandidateKeys\(imageCandidates, titleCandidates\)\)/);
  assert.match(source, /if \(open\) setSelected\(allCandidateKeys\(imageCandidates, titleCandidates\)\)/);
  assert.match(source, /type="checkbox"/);
  assert.match(source, /重试全部失败/);
});

test("failed retry dialog has a responsive modal treatment and distinct retry controls", () => {
  assert.match(styles, /\.pod-failed-retry-backdrop \{[\s\S]*?position: fixed;/);
  assert.match(styles, /\.pod-failed-retry-dialog \{[\s\S]*?max-width:/);
  assert.match(styles, /\.pod-failed-retry-confirm \{[\s\S]*?background:/);
  assert.match(styles, /\.pod-open-failed-retry/);
});

test("failed retry dialog prevents empty submission and reports selected counts", () => {
  assert.match(source, /disabled=\{busy \|\| !selectedImageStyleIndices\.length && !selectedTitleStyleIndices\.length\}/);
  assert.match(source, /图片 \{selectedImageStyleIndices\.length\} 款/);
  assert.match(source, /标题 \{selectedTitleStyleIndices\.length\} 款/);
  assert.match(source, /onSubmit\(\{ image_style_indices: selectedImageStyleIndices, title_style_indices: selectedTitleStyleIndices \}\)/);
});
