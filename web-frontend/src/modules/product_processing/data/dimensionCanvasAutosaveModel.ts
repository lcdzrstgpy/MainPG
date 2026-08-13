import type {
  DimensionCanvasItem,
  EditorState,
} from "../types/dimensionCanvas";

export interface DimensionCanvasAutosaveBaseline {
  itemId: string;
  itemRevision: number;
  sourcePreviewRevision: number;
  editorSignature: string;
}

export class DimensionCanvasAutosaveConflict extends Error {
  readonly remoteItem: DimensionCanvasItem | null;
  readonly originalCause: unknown;

  constructor(remoteItem: DimensionCanvasItem | null, originalCause: unknown) {
    super("dimension canvas revision conflict");
    this.name = "DimensionCanvasAutosaveConflict";
    this.remoteItem = remoteItem;
    this.originalCause = originalCause;
  }
}

export function persistentEditorSignature(editor: EditorState): string {
  return JSON.stringify({
    selectedAssetId: editor.selectedAssetId,
    targetSlotId: editor.targetSlotId,
    dimensions: editor.dimensions,
    annotations: editor.annotations,
    displayUnit: editor.displayUnit,
    customValueCm: editor.customValueCm,
  });
}

export function autosaveBaselineFromItem(
  item: DimensionCanvasItem,
): DimensionCanvasAutosaveBaseline {
  return {
    itemId: item.id,
    itemRevision: item.itemRevision,
    sourcePreviewRevision: item.sourcePreviewRevision,
    editorSignature: persistentEditorSignature(item.editor),
  };
}

export function adoptSafeServerRevision(
  baseline: DimensionCanvasAutosaveBaseline,
  remote: DimensionCanvasItem,
): DimensionCanvasAutosaveBaseline | null {
  if (remote.id !== baseline.itemId) return null;
  if (remote.itemRevision < baseline.itemRevision) return null;
  if (remote.sourcePreviewRevision !== baseline.sourcePreviewRevision) return null;
  if (persistentEditorSignature(remote.editor) !== baseline.editorSignature) return null;
  return autosaveBaselineFromItem(remote);
}

function isRevisionConflict(cause: unknown): boolean {
  return typeof cause === "object"
    && cause !== null
    && "status" in cause
    && Number((cause as { status?: unknown }).status) === 409;
}

type SaveWithSafeRebaseOptions = {
  baseline: DimensionCanvasAutosaveBaseline;
  editor: EditorState;
  saveAtRevision: (editor: EditorState, expectedRevision: number) => Promise<DimensionCanvasItem>;
  loadRemote: () => Promise<DimensionCanvasItem>;
};

export async function saveWithOneSafeRevisionRebase({
  baseline,
  editor,
  saveAtRevision,
  loadRemote,
}: SaveWithSafeRebaseOptions): Promise<{
  saved: DimensionCanvasItem;
  baseline: DimensionCanvasAutosaveBaseline;
  rebased: boolean;
}> {
  try {
    const saved = await saveAtRevision(editor, baseline.itemRevision);
    return { saved, baseline: autosaveBaselineFromItem(saved), rebased: false };
  } catch (cause) {
    if (!isRevisionConflict(cause)) throw cause;

    let remote: DimensionCanvasItem;
    try {
      remote = await loadRemote();
    } catch {
      throw new DimensionCanvasAutosaveConflict(null, cause);
    }
    const adopted = adoptSafeServerRevision(baseline, remote);
    if (!adopted || adopted.itemRevision <= baseline.itemRevision) {
      throw new DimensionCanvasAutosaveConflict(remote, cause);
    }

    try {
      const saved = await saveAtRevision(editor, adopted.itemRevision);
      return { saved, baseline: autosaveBaselineFromItem(saved), rebased: true };
    } catch (retryCause) {
      if (!isRevisionConflict(retryCause)) throw retryCause;
      let latest = remote;
      try {
        latest = await loadRemote();
      } catch {
        // Keep the first verified remote snapshot when the refresh itself fails.
      }
      throw new DimensionCanvasAutosaveConflict(latest, retryCause);
    }
  }
}
