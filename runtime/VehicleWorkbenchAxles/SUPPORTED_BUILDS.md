# Story Mode compatibility table

Compilation is not an acceptance test.  A row can move to **Supported** only
after exact-build signature validation and the complete in-game matrix pass.

| Target | Validated builds | Runtime status | Asset-install status |
|---|---:|---|---|
| Story Mode Legacy | None | Implemented core; adapter disabled | Managed by existing Workbench pipeline, not this runtime |
| Story Mode Enhanced | None | Implemented core; adapter disabled | Instructions only until the SDK validates its Enhanced asset installer |

## Required acceptance matrix per build

- 4-, 6-, 8-, and 10-wheel canonical fixtures report the expected wheel count.
- Arbitrary multiple steering/driven axle combinations apply without inverted
  player input or bone relocation.
- Unrelated wheel bits are unchanged.
- Repair, wheel recreation, and ownership transitions reapply safely.
- Entity deletion and runtime unload never dereference stale pointers.
- Online/network session detection performs no reads or writes.
- A signature mismatch, pointer validation failure, or unexpected wheel count
  disables safely without a crash or repeated warning.

The current table intentionally makes no claim that either edition passes these
tests.
