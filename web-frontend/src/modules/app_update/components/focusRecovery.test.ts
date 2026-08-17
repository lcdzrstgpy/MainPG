import assert from "node:assert/strict";
import test from "node:test";

import { getFocusTrapTargetIndex, shouldRecoverDialogFocus } from "./focusRecovery.ts";

test("recovers dialog focus when an update action becomes disabled or is removed", () => {
  assert.equal(
    shouldRecoverDialogFocus({ visible: true, focusedElementStillInDialog: true, focusedElementDisabled: true }),
    true,
  );
  assert.equal(
    shouldRecoverDialogFocus({ visible: true, focusedElementStillInDialog: false, focusedElementDisabled: false }),
    true,
  );
});

test("leaves enabled dialog focus and hidden dialogs alone", () => {
  assert.equal(
    shouldRecoverDialogFocus({ visible: true, focusedElementStillInDialog: true, focusedElementDisabled: false }),
    false,
  );
  assert.equal(
    shouldRecoverDialogFocus({ visible: false, focusedElementStillInDialog: false, focusedElementDisabled: true }),
    false,
  );
});

test("Tab from the dialog container enters at the first action", () => {
  assert.equal(getFocusTrapTargetIndex({ focusableCount: 2, activeIndex: -1, shiftKey: false }), 0);
});

test("Shift+Tab from the dialog container enters at the last action", () => {
  assert.equal(getFocusTrapTargetIndex({ focusableCount: 2, activeIndex: -1, shiftKey: true }), 1);
});

test("focus trap wraps at action boundaries and leaves interior actions alone", () => {
  assert.equal(getFocusTrapTargetIndex({ focusableCount: 3, activeIndex: 0, shiftKey: true }), 2);
  assert.equal(getFocusTrapTargetIndex({ focusableCount: 3, activeIndex: 2, shiftKey: false }), 0);
  assert.equal(getFocusTrapTargetIndex({ focusableCount: 3, activeIndex: 1, shiftKey: false }), null);
});
