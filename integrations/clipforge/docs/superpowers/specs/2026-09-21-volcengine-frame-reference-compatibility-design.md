# VolcEngine Frame/Reference Compatibility

## Goal

Allow ClipForge's per-shot image-to-video flow to submit valid requests to VolcEngine Seedance when a keyframe is present.

## Problem

The client builds a request with a native `first_frame` (and sometimes `last_frame`) plus product, continuity, motion, or audio reference media. VolcEngine rejects this combination with `InvalidParameter`: first/last frame content cannot be mixed with reference media.

## Design

For the `volcengine` provider:

- If a first or last frame is present, keep those native frame fields and omit all reference media from the request.
- Record a new, explicit degradation warning so persisted control summaries and UI diagnostics can explain that the reference pack was deferred for native frames.
- If no first or last frame is present, retain the existing VolcEngine reference-media behavior.

This preserves the intended image-to-video composition while complying with Ark's mutually exclusive input modes. It does not change Atlas behavior or make any billing calls.

## Tests

- A VolcEngine plan with a first frame and product/continuity/audio references has no reference inputs, preserves the first frame, and carries the new warning.
- A VolcEngine plan without frames still accepts its reference media.
- Summary sanitization accepts the new warning and continues to reject unknown warnings.

