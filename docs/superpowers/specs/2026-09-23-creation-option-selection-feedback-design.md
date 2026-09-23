# Creation option selection feedback

## Goal

Make every selectable creation option visibly selected without changing its value, default, validation, or persistence behavior.

## Scope

The common treatment applies to chip-style choices (category, language, tone, audience, platform) and card-style choices (duration, script style, visual form, output scheme, audio strategy).

## Interaction design

- Selected choices use the primary fill, white label, a white check indicator, a small elevation shadow, and a short scale transition.
- Unselected choices retain a neutral background and dark readable label; hover only increases contrast.
- Keyboard focus is independently visible through a focus ring.
- Choices remain toggleable exactly as they are today; the work is presentational only.

## Implementation boundary

Create shared class helpers in the creation UI so that chips and cards do not drift into separate visual languages. Existing `aria-pressed`, event handlers, and brief update paths remain unchanged.

## Verification

- Component-level tests prove selected and unselected helpers include the expected state attributes/styles.
- Type check and production build pass.
