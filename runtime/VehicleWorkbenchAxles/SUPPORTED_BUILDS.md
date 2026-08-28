# Story Mode compatibility table

Compilation is not an acceptance test.  A row can move to **Supported** only
after exact-build signature validation and the complete in-game matrix pass.

| Target | Validated builds | Runtime status | Asset-install status |
|---|---:|---|---|
| Story Mode Legacy | None | Signature-gated adapter compiled; no Legacy acceptance run | Blocked pending an exact-build receipt |
| Story Mode Enhanced | None | Signature-gated adapter compiled; observed build 1158 is not accepted | Blocked pending an exact-build receipt |

The observed Enhanced executable is `GTA5_Enhanced.exe` version `1.0.1158.13`,
SHA-256
`0C52864D4521D9C9D441348AA1156958792DDE8825D0297C851753F167336401`.
Recording that identity does not add it to the validated-build column.

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
