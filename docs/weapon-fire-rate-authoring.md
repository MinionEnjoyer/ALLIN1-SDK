# Weapon fire rate

The Tauri weapon workbench exposes **Rounds per minute (RPM)** for existing
`TimeBetweenShots value="..."` nodes. Read-only inspection shows the original
seconds value and derived RPM. Only a copied workspace can be edited.

RPM = 60 / seconds. Python validates 1–60,000 RPM, rejects non-finite or empty
input, rounds RPM to six decimal places, and calculates the native interval.
These are authoring safety limits, not a claim about GTA's practical fire rates.
Review shows both the RPM change and exact XML interval before confirmation.
Undo restores the original file bytes, including the original decimal formatting.
Unchanged display rounding never causes an automatic rewrite.

Missing or structurally ambiguous fields are not synthesized. An existing
invalid interval remains visible and can be repaired through a valid RPM edit.
This control does not modify animation, automatic/semi-auto, burst or other
weapon flags. Those settings can constrain actual in-game cadence.

The KRISS Vector test candidate keeps its Enhanced SMG donor value of
`0.118000` seconds: approximately **508.474576 RPM**. No new real-world Vector
rate has been assumed or applied. Editing its workspace requires a separate
package rebuild and managed update before the new rate reaches the game.
