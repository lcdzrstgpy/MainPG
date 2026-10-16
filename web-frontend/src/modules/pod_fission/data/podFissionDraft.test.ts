import assert from "node:assert/strict";
import test from "node:test";
import { createEmptyPodFissionDraft, loadPodFissionDraft, podFissionDraftStorageKey, savePodFissionDraft, type PodFissionStorage } from "./podFissionDraft.ts";
function storage(){const m=new Map<string,string>(); return {getItem:(k:string)=>m.get(k)??null,setItem:(k:string,v:string)=>void m.set(k,v),removeItem:(k:string)=>void m.delete(k)} satisfies PodFissionStorage;}
test("draft storage isolates account and workspace",()=>{const s=storage(); const d=createEmptyPodFissionDraft(); assert.equal(savePodFissionDraft("a","w",d,s).ok,true); assert.notEqual(podFissionDraftStorageKey("a","w"),podFissionDraftStorageKey("b","w")); assert.deepEqual(loadPodFissionDraft("b","w",s).state,d);});
test("invalid drafts are discarded",()=>{const s=storage(); s.setItem(podFissionDraftStorageKey("a","w"),"bad"); assert.ok(loadPodFissionDraft("a","w",s).error);});

test("spec-card configuration survives restoring the listing draft", () => {
  const s = storage();
  const draft = { ...createEmptyPodFissionDraft(), specCard: { enabled: true, style: "dark" as const, corner: "top-left" as const, display_unit: "cm" as const, cells: [["SKU", "长"], ["默认", "12"]] } };
  savePodFissionDraft("a", "w", draft, s);
  assert.deepEqual(loadPodFissionDraft("a", "w", s).state.specCard, draft.specCard);
});

test("malformed SKU rows are rejected without corrupting another workspace", () => {
  const s = storage();
  savePodFissionDraft("a", "safe", createEmptyPodFissionDraft(), s);
  s.setItem(podFissionDraftStorageKey("a", "broken"), JSON.stringify({ ...createEmptyPodFissionDraft(), listingFields: { title_mode: "long", skus: [null] } }));
  assert.ok(loadPodFissionDraft("a", "broken", s).error);
  assert.equal(loadPodFissionDraft("a", "safe", s).error, undefined);
});
