type FocusRecoveryInput = {
  visible: boolean;
  focusedElementStillInDialog: boolean;
  focusedElementDisabled: boolean;
};

type FocusTrapTargetInput = {
  focusableCount: number;
  activeIndex: number;
  shiftKey: boolean;
};

export function shouldRecoverDialogFocus({ visible, focusedElementStillInDialog, focusedElementDisabled }: FocusRecoveryInput) {
  return visible && (focusedElementDisabled || !focusedElementStillInDialog);
}

export function getFocusTrapTargetIndex({ focusableCount, activeIndex, shiftKey }: FocusTrapTargetInput) {
  if (focusableCount <= 0) return null;
  if (activeIndex < 0) return shiftKey ? focusableCount - 1 : 0;
  if (shiftKey && activeIndex === 0) return focusableCount - 1;
  if (!shiftKey && activeIndex === focusableCount - 1) return 0;
  return null;
}
