# Creation Option Selection Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make selected creation choices visibly distinct while preserving their current state and persistence behavior.

**Architecture:** A tiny presentational helper exports chip and card class builders. Existing form panels retain their own handlers and ARIA semantics, but render the helper's selected or unselected classes and a decorative check indicator for selected items.

**Tech Stack:** React, TypeScript, Tailwind CSS, Vitest.

## Global Constraints

- Do not change option identifiers, default values, click handlers, or brief persistence.
- Preserve existing `aria-pressed` and `aria-checked` values.
- Selected state must have solid primary fill, white label, check indicator, shadow, and keyboard focus visibility.

---

### Task 1: Shared option-state presentation

**Files:**
- Create: `integrations/clipforge/src/components/project-creation/option-state-styles.tsx`
- Create: `integrations/clipforge/src/components/project-creation/__tests__/option-state-styles.test.ts`

**Interfaces:**
- Produces `optionChipClass(selected: boolean)` and `optionCardClass(selected: boolean)`.
- Produces `SelectionCheck` for decorative selected-state feedback.

- [ ] **Step 1: Write the failing test**

```ts
expect(optionChipClass(true)).toContain("bg-primary");
expect(optionChipClass(true)).toContain("text-primary-foreground");
expect(optionCardClass(false)).toContain("focus-visible:ring");
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/project-creation/__tests__/option-state-styles.test.ts`

- [ ] **Step 3: Implement the helper**

```ts
export const optionChipClass = (selected: boolean) =>
  selected ? "... bg-primary text-primary-foreground ..." : "...";
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/components/project-creation/__tests__/option-state-styles.test.ts`

### Task 2: Apply shared feedback to creation controls

**Files:**
- Modify: `integrations/clipforge/src/components/project-creation/creation-brief-form.tsx`
- Modify: `integrations/clipforge/src/components/project-creation/narrative-panel.tsx`
- Modify: `integrations/clipforge/src/components/project-creation/output-scheme-panel.tsx`

**Interfaces:**
- Consumes `optionChipClass`, `optionCardClass`, and `SelectionCheck`.

- [ ] **Step 1: Replace only CSS state branches and add decorative checks to selected chips/cards.**
- [ ] **Step 2: Run the focused component tests.**

Run: `npx vitest run src/components/project-creation/__tests__/creation-brief-components.test.ts src/components/project-creation/__tests__/output-scheme-panel.test.ts src/components/project-creation/__tests__/option-state-styles.test.ts`

### Task 3: Verify the production UI

**Files:** no additional source files.

- [ ] **Step 1: Run `npx tsc --noEmit`.**
- [ ] **Step 2: Run `npm run build`.**
- [ ] **Step 3: Commit the implementation and tests.**
