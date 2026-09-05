# RPF change sets in Tauri

The **RPF Archives → Change sets** tab creates and edits inert Python change-set
documents, then exports a verified atomic plan. Saving here does not execute an
archive transaction, install an ALLIN1 package, or modify GTA.

## Workflow

1. Index an RPF in **Archive inspection**, then choose **Create change set**.
   Optionally select an exact member and use **Stage this member** to carry its
   archive layer and member path into the draft. Existing change-set JSON files
   can be opened directly without first indexing an archive.
2. Choose the new change-set JSON destination outside GTA, review its source
   archive/hash, and confirm creation. The original RPF is not copied or modified.
3. Stage **Replace file**, **Add file**, **Delete file**, **Rename entry**,
   **Create directory**, or **Remove directory**. An empty archive layer means
   the outer archive. Nested targets need their explicit layer; there is no
   basename/suffix fallback. Rename stays within one parent directory.
4. Choose payload files for add/replace. Review the exact target, payload size
   and SHA-256 before saving the action. Use separate reviews to reorder or
   remove staged actions. These actions only update the change-set JSON.
5. Review a compiled plan. Select the matching GTA installation for decoding
   when auto-detection is unsuitable. For an external RPF, explicitly select
   the folder directly containing it as the plan's workspace scope. A plan for
   the selected game's mods directory uses the existing Python mods-copy scope.
6. Review ready/blocked status, original-member evidence, action order, warnings,
   output and scope, then confirm export to a new JSON file. A blocked-scope plan
   can be saved for inspection, but it is not permission to execute it.

## Important boundaries

- Staging validates paths and stored hashes, but is not a complete tree plan.
  Compile rejects missing targets, conflicting actions, invalid directory changes,
  duplicate targets and archive-container/child conflicts through the existing
  Python planner. Each target can be changed only once in a compiled plan.
- Opening a saved document does not verify every payload. Review and compile do.
  A missing payload's staged action can be removed without recreating the document.
- Changing the document, archive, payload, action, output or scope invalidates
  prior evidence. After a failed save, refresh/review and confirm again; writes
  are never retried automatically. Unsaved drafts protect RPF tabs and navigation.
- Desktop limits are 128 staged actions, a 2-MiB document, a 16-GiB source RPF,
  512 MiB per payload, 1 GiB total payload and 1 MiB of review evidence. Initial
  archive indexing for creation is limited to 25,000 entries.
- These are local authoring plans, not portable ALLIN1 package manifests. Existing
  GXT2 package publication is a separate workflow. The adjacent **Execute & restore**
  tab now applies reviewed multi-entry plans and restores receipts on explicitly
  chosen authoring copies or existing archives in the explicitly selected GTA
  installation's `mods/` directory. Stock archives remain blocked. Advanced lock
  recovery and nested-member distribution remain to migrate.

## Execute and restore an authoring copy

1. Open the exported plan in **Execute & restore**. Select the matching decoding
   context if needed. Changing context re-inspects the selected document and clears
   its external-folder authorization.
2. Use **Authorize archive folder** to explicitly select the direct parent of the
   authoring RPF. It must match the plan's recorded workspace scope. For a mods-copy
   plan, use **Choose GTA installation** instead, explicitly selecting the matching
   installation. Auto-detection alone never authorizes a live transaction.
3. **Review execution** re-indexes the archive and compares the compiled plan with
   current source/member/payload evidence. Confirm the exact archive, current hash,
   scope and backup location before applying. GTA must be closed. Execution cannot
   be cancelled; keep the SDK open until verification finishes.
4. Success opens the new receipt with verified archive/member/backup state. Receipts
   and full-archive backups are retained under the per-user SDK `rpf-transactions`
   directory. Keep the entire transaction folder; it is not a distributable package.
5. To restore, open that `receipt.json` (or use the displayed receipt), authorize
   its archive folder, review the current and original hashes, and separately
   confirm **Restore original archive**. The original transaction's changes are
   undone together by restoring the complete outer archive snapshot.

The source plan/receipt is SHA-bound through the final Python entry point. Source
archives are rechecked immediately before the staged apply/restore commit. Changed
archives, missing/corrupt backups, open GTA, occupied archive locks, insufficient
space and stale confirmation fail closed. Opening a receipt can still report a
missing backup; it cannot authorize restore. An already restored receipt does not
offer rollback again. On failure, inspect the returned receipt path before retrying;
the UI never retries automatically or removes locks without a separate review.

## Live mods writes and recovery

The native shell grants a dedicated `--allow-rpf-writes` capability, separate from
managed-package writes and the general console game-write flag (which remains off).
Both **archive confirmation** and **GTA is closed / authorize this mods archive**
must be checked for live apply or rollback. The physical game root must match the
plan target; stock, other-installation and redirected paths are rejected. The
same scope and document are checked again immediately before commit. Tests do not
install anything into the real game's archives.

**Refresh transaction history** lists retained SDK receipts, including malformed
entries as errors. It scans at most 256 folders and reports truncation; use the
receipt picker for unlisted folders. Listing shows recorded status, not integrity.
Opening a receipt performs native verification.

**Review receipt recovery** reconciles an interrupted record to `applied` or
`interrupted_before_commit` only when archive entries and the original backup verify.
Its separate confirmation writes receipt metadata only, never an RPF. Archive,
receipt, backup and lock changes invalidate the review. Active lock owners block
recovery. A stale lock is reported and retained: this screen does not automatically
clear it, replay a plan, restore a missing archive or overwrite external changes.

### Reviewed stale-lock cleanup

After reconciling an interrupted receipt, use **Review stale lock cleanup** in the
Review operation pane. Cleanup requires a settled receipt matching the verified
archive state, an intact original backup, a matching lock plan identity and an
exited owner. Missing/foreign/malformed locks, active or unverifiable owners,
linked paths, external changes and unsupported platforms remain blocked.

The review shows the exact lock and the retained `cleared-lock-<sha256>.json` beside
the receipt. Confirm **Clear stale lock only**. Mods-folder locks require the
separate game-write checkbox and native RPF authority; GTA must be closed for any
cleanup. Archive, receipt and backup are not rewritten, and no changes are replayed.

On local Windows volumes, the SDK holds the lock exclusively, verifies its bytes
and file identity, retains an exact flushed copy, repeats the transaction and owner
checks, and deletes that same open file handle. There is no pathname-unlink fallback.
An exact existing retained copy may be reused after a fresh review; different
evidence is never overwritten. If cleanup fails after retention, both copies remain.
This uses the documented Windows [exclusive file sharing](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
and [handle-bound disposition](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle)
APIs. Missing-archive recovery and general orphan-lock cleanup remain out of scope.

## Validation

Python tests cover all six staging actions, ordered edits, source/document/payload
drift, final commit/compile races, same-size source changes, bounds, unsafe outputs
and explicit scope. React tests cover confirmation, exact-member handoff, keyboard
focus, stale/cancelled jobs, malformed evidence and navigation guards.

The extended frozen-sidecar smoke builds generated OPEN RPF fixtures, stages root
and nested replacements plus a directory, exports one native-verified plan, and
checks the original RPF SHA-256 is unchanged. Real GTA supplies read-only decoding
context; it is not a test write target. Native-dialog and clean-machine validation
remain separate.

The extended smoke also executes that root/nested plan on the generated archive,
verifies its retained receipt/backup, refuses rollback after an external fixture
edit, and restores the exact original checksum through a freshly confirmed rollback.
