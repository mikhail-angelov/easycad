# SPEC20 task list

## 1. Responsive workspace (W1/W2)

- [x] Define wide, medium, and narrow layouts in the shared stylesheet.
- [x] Hide code by default and persist the explicit toggle in `localStorage`.
- [x] Conditionally mount Monaco only after the user asks to show code.
- [x] Keep the editor as an overlay below 1024px and a full-screen overlay on phones.
- [x] Preserve viewer resizing with its existing `ResizeObserver`.

## 2. Chat composition (W3)

- [x] Auto-size both prompt textareas, including retry restoration and send reset.
- [x] Cap textarea height and add the Enter/Shift+Enter hint.

## 3. Geometry contract and readout (W4)

- [x] Make `Step.code` base source at add/import boundaries.
- [x] Reattach geometry only for triage/generation calls, safely when no geometry exists.
- [x] Return base source from execute/manual/history/export paths.
- [x] Parse worker facts once in the frontend and render localized EN/RU stats in the viewer and variations.
- [x] Add API and frontend regression tests for the invariant and formatter.

## 4. Legible controls (W5)

- [x] Add visible labels for code, save/load/new, refine, and variations controls.
- [x] Make the exhausted trial pill an account-flow CTA; keep positive balances neutral.

## 5. Verification

- [x] Run frontend build and focused UI tests.
- [x] Run the complete backend test suite.
- [ ] Inspect 375px, 768px, and 1440px with both editor states before release.
