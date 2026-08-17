import { useCallback, useEffect, useRef, useState } from "react";

import { getDimensionItem } from "../api/dimensionCanvasApi";
import {
  adoptSafeServerRevision,
  autosaveBaselineFromItem,
  DimensionCanvasAutosaveConflict,
  persistentEditorSignature,
  saveWithOneSafeRevisionRebase,
  type DimensionCanvasAutosaveBaseline,
} from "../data/dimensionCanvasAutosaveModel";
import type {
  DimensionCanvasItem,
  EditorState,
  SaveDimensionItemRequest,
} from "../types/dimensionCanvas";

type AutosaveState = "idle" | "saving" | "saved" | "error";

type QueuedSave = {
  generation: number;
  editor: EditorState;
};

export function useDimensionCanvasAutosave(
  item: DimensionCanvasItem | null,
  editor: EditorState,
  save: (request: SaveDimensionItemRequest) => Promise<DimensionCanvasItem>,
): {
  state: AutosaveState;
  retry: () => void;
  error: string;
  conflictItem: DimensionCanvasItem | null;
  savedItem: DimensionCanvasItem | null;
  retryable: boolean;
} {
  const [state, setState] = useState<AutosaveState>("idle");
  const [error, setError] = useState("");
  const [conflictItem, setConflictItem] = useState<DimensionCanvasItem | null>(null);
  const [savedItem, setSavedItem] = useState<DimensionCanvasItem | null>(null);
  const [retryable, setRetryable] = useState(false);
  const timerRef = useRef<number | null>(null);
  const itemIdRef = useRef("");
  const revisionRef = useRef(0);
  const generationRef = useRef(0);
  const inFlightRef = useRef(false);
  const queuedRef = useRef<QueuedSave | null>(null);
  const lastSignatureRef = useRef("");
  const blockedRef = useRef(false);
  const saveRef = useRef(save);
  const baselineRef = useRef<DimensionCanvasAutosaveBaseline | null>(null);

  saveRef.current = save;

  const buildRequest = (snapshot: EditorState, expectedRevision: number): SaveDimensionItemRequest => ({
    expected_revision: expectedRevision,
    selected_source_asset_id: snapshot.selectedAssetId,
    target_slot_id: snapshot.targetSlotId,
    physical_dimensions: snapshot.dimensions,
    annotations: snapshot.annotations,
    canvas_settings: {
      fit: "contain",
      style: "auto",
      display_unit: snapshot.displayUnit,
      custom_value_cm: snapshot.customValueCm,
      endpoint_style: snapshot.endpointStyle,
    },
  });

  const flush = useCallback(async () => {
    const queued = queuedRef.current;
    if (!queued || !itemIdRef.current || inFlightRef.current) return;
    queuedRef.current = null;
    inFlightRef.current = true;
    const requestItemId = itemIdRef.current;
    const requestSave = saveRef.current;
    const baseline = baselineRef.current;
    if (!baseline || baseline.itemId !== requestItemId) {
      inFlightRef.current = false;
      return;
    }
    let failed = false;
    setState("saving");
    setError("");
    setRetryable(false);
    try {
      const outcome = await saveWithOneSafeRevisionRebase({
        baseline: { ...baseline, itemRevision: revisionRef.current },
        editor: queued.editor,
        saveAtRevision: (snapshot, expectedRevision) => requestSave(buildRequest(snapshot, expectedRevision)),
        loadRemote: () => getDimensionItem(requestItemId),
      });
      if (itemIdRef.current !== requestItemId) return;
      const saved = outcome.saved;
      revisionRef.current = saved.itemRevision;
      baselineRef.current = outcome.baseline;
      if (queued.generation === generationRef.current) {
        setSavedItem(saved);
        setState("saved");
        setConflictItem(null);
      }
    } catch (cause) {
      if (itemIdRef.current !== requestItemId) return;
      failed = true;
      blockedRef.current = true;
      if (cause instanceof DimensionCanvasAutosaveConflict) {
        setConflictItem(cause.remoteItem);
        setRetryable(false);
        setError("预检或画布版本已变化。本地编辑仍保留，请刷新对比后再保存，系统不会静默覆盖。" );
      } else {
        const message = cause instanceof Error ? cause.message : String(cause);
        setRetryable(true);
        setError(message || "自动保存失败，本地编辑仍保留");
      }
      setState("error");
    } finally {
      inFlightRef.current = false;
      if (queuedRef.current && !failed && !blockedRef.current) {
        void flush();
      }
    }
  }, []);

  useEffect(() => {
    if (!item) {
      itemIdRef.current = "";
      queuedRef.current = null;
      setState("idle");
      return;
    }
    itemIdRef.current = item.id;
    revisionRef.current = item.itemRevision;
    baselineRef.current = autosaveBaselineFromItem(item);
    generationRef.current = 0;
    queuedRef.current = null;
    lastSignatureRef.current = persistentEditorSignature(item.editor);
    setState("idle");
    setError("");
    setConflictItem(null);
    setSavedItem(null);
    setRetryable(false);
    blockedRef.current = false;
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
  }, [item?.id]);

  useEffect(() => {
    if (!item || item.id !== itemIdRef.current || !baselineRef.current) return;
    const adopted = adoptSafeServerRevision(baselineRef.current, item);
    if (!adopted || adopted.itemRevision <= baselineRef.current.itemRevision) return;
    baselineRef.current = adopted;
    revisionRef.current = Math.max(revisionRef.current, adopted.itemRevision);
  }, [item?.editor, item?.id, item?.itemRevision, item?.sourcePreviewRevision]);

  useEffect(() => {
    if (!item || item.id !== itemIdRef.current) return;
    const signature = persistentEditorSignature(editor);
    if (signature === lastSignatureRef.current) return;
    lastSignatureRef.current = signature;
    generationRef.current += 1;
    queuedRef.current = { generation: generationRef.current, editor };
    if (blockedRef.current) return;
    setState("saving");
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      void flush();
    }, 250);
    return () => {
      if (timerRef.current != null) window.clearTimeout(timerRef.current);
    };
  }, [editor, flush, item]);

  useEffect(() => () => {
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
  }, []);

  const retry = useCallback(() => {
    if (!item || !retryable) return;
    blockedRef.current = false;
    generationRef.current += 1;
    queuedRef.current = { generation: generationRef.current, editor };
    setConflictItem(null);
    setRetryable(false);
    setState("saving");
    setError("");
    void flush();
  }, [editor, flush, item, retryable]);

  return { state, retry, error, conflictItem, savedItem, retryable };
}
