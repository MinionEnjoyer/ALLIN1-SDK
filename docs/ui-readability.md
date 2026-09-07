# Launcher-aligned SDK readability

The SDK uses the launcher's Segoe UI typography, green accents, quiet light/dark
surfaces, 5px controls, 6px section treatments and 8px confirmation dialogs.
Existing navigation, keyboard shortcuts, review/confirmation gates, and authoring
operations remain unchanged.

## Type and controls

- Body: 14px; labels/actions: 13px; captions, identifiers and helper text: 12px.
- Workspace titles: 26px; pane headings: 18px.
- Buttons: at least 36px; text/select inputs: at least 38px; navigation: 41px.
- Sizes use rem tokens for text scaling. Do not shrink helper copy to fit a pane.
- Normal and secondary text meet 4.5:1 contrast on both canvas and raised surfaces
  in light and dark themes (covered by `Readability.test.ts`). Disabled controls
  are visually distinguished; they are not used as the contrast reference.

`desktop/src/readability.css` is imported after the base stylesheet. Component
styles use `--font-caption`, `--font-label`, and `--font-body`; lazy-loading a
workbench must not restore tiny local type. Canvas graph and code-editor metrics
remain tool-owned rather than using blanket zoom or transform scaling.

## Layout and verification

Headings and toolbars wrap; weapon pane headers grow with their text. Narrow
windows stack multi-pane editors, while large windows keep the existing parallel
inspection layout. Sliders retain numeric entry and reset controls. Confirmation
actions remain reachable in their scrollable dialog.

Run `pnpm exec vitest run` and `pnpm build` from `desktop`. Visual checks should
include the vehicle and weapon workbenches, calibration, Models & Materials,
Package Linker/review, both themes, expanded/collapsed navigation, and narrow
windows. Browser preview fixtures exercise presentation only; they do not prove
native decoding, game writes, or in-game calibration.
