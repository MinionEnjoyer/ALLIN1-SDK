# Unsigned release preparation and future signing

## 0.6.4 distribution policy

0.6.4 is being prepared as an **unsigned manual-download release**, not a
SignPath-certified release. No certificate provider or approval date is promised.
This policy choice is not release approval. The source is still unreleased;
the [release checklist](docs/release-0.6.4.md) retains the remaining gates.

1. Identify and review one clean source revision, version and dependency set.
2. Run full automated gates and build a fresh candidate from those exact inputs.
3. Verify the shell, sidecar, installer, portable payload, resources and embedded
   identities agree. Record actual signature status without claiming a project
   publisher certificate. Preserve any genuine third-party signatures.
4. Exercise isolated Windows install, upgrade, repair, uninstall and rollback,
   including missing dependencies, spaces/long paths and user-data preservation.
5. Obtain the required independently bound acceptance for the exact final
   artifacts. Obtain separate approval before launching GTA or publishing.
6. Review only the current [release notes](RELEASE_NOTES.md), disclose unsigned
   distribution, and include final checksums and build identity with the assets.

Do not rename candidate binaries or mix stale staged/packaged files to imply a
new release. A portable package is a complete payload, not just its executable.
Windows may warn about an unknown publisher or reputation; never recommend
disabling security protections. SHA-256 checksums do not authenticate a publisher.

## Update signatures are separate

Windows publisher signing and Tauri updater signing serve different purposes.
The [Tauri updater requires its own signature verification](https://v2.tauri.app/plugin/updater/#signing-updates);
choosing an unsigned manual release does not switch that requirement off.
React update installation remains disabled pending its trusted metadata/key
workflow. Legacy update services must retain all existing verification checks.
No update keys or signing secrets were created or changed by this preparation.

## GitHub release presentation

`README.md` has one current “What's new” section. `RELEASE_NOTES.md` describes
only 0.6.4; prior releases are preserved in the
[history archive](docs/archive/release-notes-before-0.6.4.md).
Do not copy the old README or append GitHub's generated commit list to a release.

```powershell
python scripts/release_notes.py --version 0.6.4 --output build/release-notes.md
```

This validates and renders a local draft; it does not publish or approve a
release. `--require-final` rejects an unreleased draft and should be used at the
eventual publication boundary after the separate release gates are satisfied.
Use that curated body as the eventual GitHub release notes file. Links are bound
to the selected tag when rendered, not to a moving default branch.

The Tauri CI workflow builds unsigned candidates and uploads evidence; it has no
release-publishing permission. The old `ci-release.yml` Tkinter build/publishing
jobs have been removed so tagging 0.6.4 cannot select them.

## Future signing integration — inactive

The obsolete Azure/Tk workflow has been removed, not retained as an alternative
release path. No signing service is configured or promised for unsigned 0.6.4.

Reactivating a signed channel requires a new reviewed workflow for the actual
Tauri artifacts, a provisioned trust identity, signature verification and
rehashing of final signed bytes and receipts. Restoring the old Tkinter job is
not a Tauri release procedure. See the [signing policy](CODE_SIGNING_POLICY.md).
