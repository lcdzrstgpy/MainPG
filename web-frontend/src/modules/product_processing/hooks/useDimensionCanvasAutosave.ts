import { useCallback, useEffect, useRef, useState } from "react";

import { getDimensionItem } from "../api/dimensionCanvasApi";
import { PpRequestError } from "../api/client";
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

function persistentSignature(editor: EditorState): string {
  return JSON.stringify({
    selectedAssetId: editor.selectedAssetId,
    targetSlotId: editor.targetSlotId,
    dimensions: editor.dimensions,
    annotations: editor.annotations,
  });
}

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
} {
  const [state, setState] = useState<AutosaveState>("idle");
  const [error, setError] = useState("");
  const [conflictItem, setConflictItem] = useState<DimensionCanvasItem | null>(null);
  const [savedItem, setSavedItem] = useState<DimensionCanvasItem | null>(null);
  const timerRef = useRef<number | null>(null);
  const itemIdRef = useRef("");
  const revisionRef = useRef(0);
  const generationRef = useRef(0);
  const inFlightRef = useRef(false);
  const queuedRef = useRef<QueuedSave | null>(null);
  const lastSignatureRef = useRef("");
  const blockedRef = useRef(false);
  const saveRef = useRef(save);

  saveRef.current = save;

  const buildRequest = (snapshot: EditorState): SaveDimensionItemRequest => ({
    expected_revision: revisionRef.current,
    selected_source_asset_id: snapshot.selectedAssetId,
    target_slot_id: snapshot.targetSlotId,
    physical_dimensions: snapshot.dimensions,
    annotations: snapshot.annotations,
    canvas_settings: { fit: "contain", style: "auto" },
  });

  const flush = useCallback(async () => {
    const queued = queuedRef.current;
    if (!queued || !itemIdRef.current || inFlightRef.current) return;
    queuedRef.current = null;
    inFlightRef.current = true;
    let failed = false;
    setState("saving");
    setError("");
    try {
      const saved = await saveRef.current(buildRequest(queued.editor));
      revisionRef.current = saved.itemRevision;
      if (queued.generation === generationRef.current) {
        setSavedItem(saved);
        setState("saved");
        setConflictItem(null);
      }
    } catch (cause) {
      failed = true;
      blockedRef.current = true;
      const message = cause instanceof Error ? cause.message : String(cause);
      if (cause instanceof PpRequestError && cause.status === 409) {
        try {
          const remote = await getDimensionItem(itemIdRef.current);
          setConflictItem(remote);
        } catch {
          setConflictItem(null);
        }
        setError("预检或画布版本已变化。本地编辑仍保留，请刷新对比后再保存，系统不会静默覆盖。" );
      } else {
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
    generationRef.current = 0;
    queuedRef.current = null;
    lastSignatureRef.current = persistentSignature(item.editor);
    setState("idle");
    setError("");
    setConflictItem(null);
    setSavedItem(null);
    blockedRef.current = false;
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
  }, [item?.id]);

  useEffect(() => {
    if (!item || item.id !== itemIdRef.current) return;
    const signature = persistentSignature(editor);
    if (signature === lastSignatureRef.current) return;
    lastSignatureRef.current = signature;
    generationRef.current += 1;
    queuedRef.current = { generation: generationRef.current, editor };
    blockedRef.current = false;
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      void flush();
    }, 450);
    return () => {
      if (timerRef.current != null) window.clearTimeout(timerRef.current);
    };
  }, [editor, flush, item]);

  useEffect(() => () => {
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
  }, []);

  const retry = useCallback(() => {
    if (!item) return;
    generationRef.current += 1;
    queuedRef.current = { generation: generationRef.current, editor };
    setConflictItem(null);
    void flush();
  }, [editor, flush, item]);

  return { state, retry, error, conflictItem, savedItem };
}
