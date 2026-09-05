# SDK release-hardening audit — 2026-09-04

> Historical checkpoint: results, commands and artifact identities below apply
> only to the described source/session. They do not qualify current 0.6.4.
> See the [current release guide](release-0.6.4.md) before using this as guidance.

## Release decision: FAIL / not qualified for distribution

This is an SDK audit, not a transposition of the Launcher audit. The source fixes
below are local and uncommitted. No GTA process was started, no real game files
were used for destructive tests, no installed SDK files were changed, and no
release was published. The real installed SDK/registration was inspected read-only.
Only disposable NSIS guard harnesses were executed; the real installer was not run.

The source version is now **0.6.4** in Python, pyproject, npm, Cargo (including
lockfile), and Tauri. Browser preview version reporting reads package.json rather
than another literal. No remote `v0.6.4*` tag was returned by the read-only tag
check during this audit. No tag or commit was created.

A new unsigned 0.6.4 test candidate was built and sealed after exact extraction
checks and packaged sidecar/ped smokes. Its build ID is
`3bc04b8d48df46d089db71d1d8919455`. This is not a distribution-ready release:
clean reviewed source, actual Windows installer lifecycle, signing, and live
acceptance remain unqualified. The user's planned SDK and Launcher release is
0.6.4; this pass changed only the SDK, not Launcher source or release assets.

The older 0.1.0 installer and frozen 0.6.3 sidecar predate these fixes. Their
initial identities below are historical evidence, not hardened artifacts.

## Prioritized findings and disposition

| Priority | SDK finding and reproducible evidence | Disposition |
| --- | --- | --- |
| P0 | The old swap helper resolved `SDK.previous` before recursive deletion. A disposable junction to a sibling canary directory caused the canary to be deleted. On this Windows host the subsequent rename then failed with WinError 5. | Fixed: no destructive reuse of `.previous`; validated, unique, retained backup roots. Reparse checks occur before resolution. |
| P1 | The old helper accepted a staged fixture with no checksums/release metadata and even non-PE bytes. Download-time checks did not survive the handoff to the swap process. | Fixed: complete staging inventory, hashes and entrypoints reverified at consumption; scheduled helper receives the pinned manifest hash and rejects substitution. |
| P1 | Successful old swaps deleted the entire previous installation, including install-local user-data canaries. | Fixed: retained uniquely named backups. Failed launch restores the original installation and retains the failed candidate. User files are preserved in the backup, not automatically merged into the new application tree. |
| P1 | Managed-path checks resolved the supplied root before checking aliases. Rollback receipt paths were not comprehensively checked against their distinct backup/applied roots before actions. | Fixed: check root/ancestors before resolution; reject duplicate destinations and cross-owner backup/applied paths; preflight all receipt paths before mutations. Receipt temporary files use exclusive creation and unique names. |
| P1 | ZIP validation admitted noncanonical Windows aliases/special names; private extraction could write an earlier member before rejecting a later member. JSON duplicate keys could be silently overwritten. | Fixed: canonical relative paths, file/parent collisions, case collisions, special/reparse entries and duplicate JSON keys rejected. Extraction preflights the full path set. Windows long-path regression remains exercised. |
| P1 | Legacy release packaging merged resource trees into an existing app directory, retaining removed resources/helpers; nested `checksums.json` files were incorrectly omitted from the payload manifest. Existing release filenames could be overwritten. | Fixed: fresh private packaging tree, exact archive/manifest verification, nested checksum files included, existing archive identities refused. Input app directory is not destructively cleaned. Tauri native publish uses a fresh unique directory; sidecar resource mapping is exact, not `*.exe`. |
| P1 | Python/source reported 0.6.3, Tauri/npm/Cargo/installed registration reported 0.1.0. Staged and installed shells, sidecars and RPF helpers have different hashes under the same visible desktop version. | Source versions aligned to new 0.6.4. Actual older artifacts are identified below, not relabeled. Clean reviewed source/version checks added to release packaging. Rebuild and clean-machine qualification remain required. |
| P1 | Tauri's default pre-install hook runs after `SetOutPath`, which can already create a redirected destination. The template also trusted registry strings for an old-file deletion and previous uninstaller command. | Pinned/customized NSIS template checks before destination creation and before uninstall; registry-controlled executable/deletion paths removed. 26 compiled guard tests passed in disposable directories. Full product installer lifecycle is still NOT TESTED. |
| P1 | Source version and installed Python distribution metadata could disagree (0.6.4 vs 0.6.3 in this build environment). Frozen/staged components had no common build binding. | Refreshed the local editable SDK metadata without changing dependencies; candidate preparation now rejects stale metadata. Shell, frozen sidecar, and resources carry one identity, including dirty source digest, submodule, lockfiles and tool/dependency versions. Shell/sidecar mismatches and stale runtime resource manifests fail closed. |
| P1 | Native receipt dates only had to parse; stale or future dates were accepted. Existing native receipts bind PE hash, edition, exports and required checks, but do not independently establish an SDK live acceptance session. | Date/type/duplicate-key checks hardened. Separate exact-schema live qualifier requires actual artifact/dependency/evidence bytes, independent pinned identity/session, complete checks, edition and freshness. Legacy runtime receipts and PE validity alone cannot pass this gate. |
| P2 | The SDK's schema boundary test still asserted that only mod schemas 1/2 existed after exact-member schemas 3/4 had been implemented. The first full run failed on the stale expectation, not on accepting the invalid fixture. | Corrected to reject unknown schema 5, while separately verifying that changing a schema-2 extension document to 3/4 does not bypass their field restrictions. No validation threshold was lowered. |

### Launcher finding not reproduced in the SDK

`inspect_release_archive` already required `set(checksums) == archive payloads`
and validated ZIP names. A `../canary` checksum key without a matching payload
was rejected before extraction. The added adversarial test preserves that
protection. The confirmed SDK escape was the **backup junction cleanup**, not
the Launcher's separate checksum-key exploit.

### Reproducing the original defects safely

From the SDK checkout, with its development Python environment:

```powershell
python scripts/audit_release_baseline.py
python -m pytest tests/test_release_hardening.py tests/test_self_update.py -q
```

The baseline script loads only `updater_host.py` from commit
`bfc4e010126efe3a549adb96cbe9a4c855c80db3`. It creates its own fresh temporary
installation, stage, junction and canaries, checks the resolved cleanup target
is inside that temporary tree, and stubs executable launch. Its output recorded:

```json
{
  "outside_destination_canary_deleted": true,
  "unverified_stage_accepted": true,
  "install_local_user_data_deleted": true
}
```

Only disposable test canaries were deleted by the old-code reproduction. The
corresponding current-code tests preserve both outside canaries and backup user
data. No real user/game file is an adversarial test target.

## Qualification and telemetry contracts

`release_qualification.py` is the authoritative executable acceptance schema
version 1; it does not start a game or generate live receipts. Exact fields are:

- Report: schema_version, kind, suite, session_id, target_edition, identity,
  started_at, ended_at, checks, events_path, events_sha256, synthetic.
- Identity: exact sdk_commit, build_id, source_tree_sha256, sdk_version,
  nonempty artifact/dependency hash maps, and schema_versions.
- Independent session anchor: schema_version, session_id, suite, edition,
  identity, start/end, events hash and authority. Identity and session files are
  pinned separately by the release authority, not by values supplied in the
  report being evaluated.
- Each JSONL event: schema_version, sequence, session_id, timestamp, type,
  identity, target_edition, check, status, evidence. A complete ordered start /
  check / end sequence, per-check evidence files, and matching hashes are
  required. Unknown/missing fields, duplicate checks, unrelated identities,
  old event versions, skipped/failed checks and synthetic claims fail closed.

The freshness policy for this gate and runtime receipt consumption is seven
days, with timezone-aware timestamps; future/reversed sessions fail. Evidence
must be collected again after material artifact/dependency changes. The gate
does not make a cryptographic claim about an untrusted self-authored anchor:
**the external trust pins must come from independently reviewed QA evidence**.
It verifies that anchor's actual files and semantic session/check structure;
hashes alone cannot establish the truth of a human visual observation.

Required `sdk-desktop` checks: clean_install, upgrade, repair, uninstall,
rollback, missing_dependencies, space_paths, long_paths,
user_data_preservation, ped_authoring, native_preview, texture_preview,
cancel_close. Separate results remain `package_integrity`, `automated_tests`
and `live_acceptance`; passing one does not imply either of the others.

The SDK has no `price=` weapon-purchase smoke parser corresponding to the
Launcher's reported drift. Its desktop exchange is structured protocol 1.0.0.
The packaged smoke now emits a versioned `automated_test_result` with the actual
sidecar version, binary hash and resource-manifest hash, rechecked after the
run. Unavailable commit identity is null, and live acceptance is NOT TESTED.

SDK React/native previews and optional Blender renders are not the in-game
Reactor renderer. The separate `reactor-story` qualification suite requires
Reactor load, renderer initialization, a presented frame, resize/device
recovery, shutdown and online guard evidence. No SDK preview image, PE export,
Blender render or generic purchase log is accepted as a Reactor session. The
SDK does not currently supply an independently trusted Reactor live collector;
that integration remains unqualified rather than being simulated as a pass.

The signed publishing workflow now has a fail-closed live qualification step
before publishing. It requires externally provisioned evidence in the runner's
temporary evidence directory and independently supplied identity/session pins
for both editions. Provisioning is deliberately not fabricated by this audit;
without it, publishing fails. This workflow was edited locally, not executed.

## Release matrix

PASS below applies only to the stated scope, not to overall release readiness.

| Gate | Result | Evidence / limitation |
| --- | --- | --- |
| Checksum-key traversal, canonical path and pre-write rejection | PASS | Disposable ZIP/manifest/canary regressions. |
| Junction-root and rollback backup containment | PASS | Actual Windows junction tests; old-code reproduction plus fixed-code preservation. |
| Update staged-byte/manifest substitution | PASS | Missing payload, changed binary, extra file, changed manifest and independently pinned manifest cases. |
| Isolated package clean install / upgrade / enable-disable / uninstall / rollback | PASS | Existing SDK lifecycle suite plus added receipt/containment tests; synthetic directories/markers, no real GTA. |
| Space-containing and long Windows paths | PASS | Updater staging and failure rollback tests, including extended paths. |
| Missing optional runtime/dependency behavior | PASS | New frozen 0.6.4 and installer-extracted sidecar/ped smokes, isolated user state and system-only PATH; no system Python/.NET or Launcher needed for these tested flows. Missing WebView2 on a clean machine remains NOT TESTED. |
| Python complete test suite and configured coverage | PASS | Final run: 2,215 passed, 6 skipped; 80.06% against unchanged 80%. No warnings in this final run. Earlier Tk warnings/skips remain preserved in their own logs. |
| React tests / production TypeScript-Vite build | PASS | 194 tests / 19 files; production build completed. Existing large-chunk warning remains. |
| Rust broker tests | PASS | 11 tests, including same-version/different-build rejection; 0.6.4 release shell compiled. |
| Native RPF exact-member checks | PASS | 66 checks, no game required. |
| Portable axle C++ compile / CTest | PASS | SDK native-core test compiled and ran CTest in a disposable build directory. Not game acceptance. |
| Legacy release ZIP generated consistency | PASS | Exact manifest/archive bytes, stale-resource exclusion and no-overwrite regression tests. |
| Candidate staged/resource/package inventory | PASS | 270 resource files plus shell, sidecar and resource manifest: 273 exact payloads; six explicit installer plugins/assets and generated uninstaller are separately hashed. |
| Existing installed declared resource hashes | PASS | 265 declared files matched their installed manifest, read-only. Extra user files were not treated as package-owned. |
| Existing NSIS checksum companion | PASS | Matches the old installer hash below; not a payload/acceptance result. |
| Candidate source/staged/packaged identity agreement | PASS | 0.6.4 in source, Windows shell/sidecar file versions, runtime handshake and candidate identity. Exact NSIS staging capture is compared to extracted bytes. |
| Existing installed SDK vs candidate identity | FAIL | Existing registered 0.1.0 SDK was intentionally not upgraded; it is not this candidate. |
| Clean reviewed commit for new release | FAIL | HEAD is still bfc4e01… and the migration/hardening tree is dirty. No review or clean commit is claimed. |
| Rebuilt hardened 0.6.4 frozen payload and installer | PASS | Candidate pipeline completed; extracted sidecar and ped workflow smokes passed. No real installer execution. |
| Generated NSIS embedded payload vs tested stage | PASS | Complete extracted file set and all 273 payload hashes matched independently captured/staged files; installer hash rechecked after smoke. |
| Actual NSIS clean install / upgrade / repair / uninstall / rollback | NOT TESTED | No disposable Windows desktop environment configured. This profile has an existing registered SDK; running the installer here would not be isolated. |
| Compiled NSIS guard junction/hard-link/space-path/user-data canaries | PASS | 26 tests execute the actual shared guard functions for install and uninstall; no product registration, shortcuts or real user-data cleanup. |
| Positive long-path NSIS installation | FAIL | The guard currently rejects paths over 240 characters (including full payload paths) before writing. Safe rejection is tested; long-path installation support is not implemented. This does not change the separate updater long-path result. |
| Actual NSIS lifecycle with interrupted update / optional user-data removal | NOT TESTED | Guard harness execution is not full product lifecycle execution. |
| Release code signing | FAIL | Candidate Authenticode status is `NotSigned`; no signing/publishing was attempted. |
| Native symlink-privilege tests | NOT TESTED | Privilege-dependent cases remain skipped. Actual directory-junction tests passed separately; they do not convert symlink skips into passes. |
| Private suppressor fixture / real Enhanced acceptance | NOT TESTED | Deliberately not run against real game installations. |
| Independently verified live SDK / Reactor acceptance, both editions | NOT TESTED | No authority-pinned live evidence for the exact future 0.6.4 artifacts. |

The first full run recorded 2,142 passed, one stale-contract-test failure, six
skipped and 79.79% coverage (FAIL). That record is retained, not replaced by a
claim that all earlier checks passed. Coverage exclusions and fail_under were
not lowered or enlarged to obtain a passing gate.

## Exact historical artifact identities

HEAD: `bfc4e010126efe3a549adb96cbe9a4c855c80db3` (dirty source checkout).
The final source snapshot digest/input count is in the generated read-only
identity report, avoiding a self-referential digest inside this source document.

| Artifact | SHA-256 |
| --- | --- |
| Existing `ALLIN1 SDK_0.1.0_x64-setup.exe` | `a01bbce7c4ab79eedd3474cf5f2689aeff26bc99498615ae589237c16bbd62f5` |
| Initially staged release shell | `96156f6f6081cf0c501df119b72e270b02caf6c1dc9d3eaae45f7afefb7f51ea` |
| Initially staged frozen sidecar, reports 0.6.3 | `db726c17ad40433dd7b44fee74267dfe3a6aa7ef57138a43a453fb739852c07d` |
| Initially staged RpfPatcher.dll | `39acd1c787231987c12746cc06d4e300739881d921e5d299c04a4d3af13c2afc` |
| Installed shell, registration 0.1.0 | `b342f168cbd126441253a69268db57c031b617d5d8eb2f9e6e3afcbb47dcb4f4` |
| Installed sidecar | `3f44ef58d529265fc7775c1d33a4469e6e0e228ab02c121a9737d95bd3244f97` |
| Installed RpfPatcher.dll | `6fd276adc28327ab30350e8f85e7dddcc01da6c7be5df1e8f84da48d17ebfd7c` |

These mismatches identify different older builds; they are not evidence that
the installed files are corrupt (their own resource manifest matched). Existing
binaries have no trustworthy complete binding to the dirty source commit/tree.

## Exact new candidate identity

Candidate: `ALLIN1-SDK-0.6.4-candidate-3bc04b8d48df-setup.exe`, 68,688,041 bytes.
Build ID: `3bc04b8d48df46d089db71d1d8919455`.
Source snapshot: `10ad96a1b4f52b4642b9a0f6819f7d28ba461c57f71a0c9618b8ff87c36360b7`
(477 inputs, dirty HEAD shown above). This report was updated after that build;
the candidate records its actual build-time snapshot, not the later report edit.
CodeWalker submodule: `0bf552913d96da9ad1f266eb5c7d6d75b96c89f2`.

| Candidate component | SHA-256 |
| --- | --- |
| Installer | `a7aacec79129a97bcd480d2d98f13423a6f19a08c14287944a7805aea84ca070` |
| NSIS-staged and packaged shell | `3a3b9b69f7685c1bcdcba74f9170134937e724de153ef97df569c07c7f179fb2` |
| Sidecar | `585ec32ac368341a399d10082d1a58abf5d540b4f230581eec2bf3f0dda9251f` |
| RpfPatcher.dll (fresh publish, same source bytes) | `39acd1c787231987c12746cc06d4e300739881d921e5d299c04a4d3af13c2afc` |
| Resource manifest | `295883ee286b5f90184736f0896990d346bbf12357974f2374cecb95b9518e0f` |
| Restored compiler-output shell (not the packaged artifact) | `00b80db19da8e39cc9814307d0a37ce932ce2f8c5e79e96ec80a1b10ef9ac9f4` |

The first attempted candidate (`36ee69147d5a4c3ebd3284a245aa6692`) failed exact
shell comparison and has no successful validation receipt. Investigation found
only the expected three-byte `UNK` → `NSS` marker change, not stale application
code. Tauri patches the shell for NSIS and restores the compiler binary afterward.
The pipeline now captures the actual NSIS-stage shell before `File` packaging;
it does not excuse arbitrary byte differences. [Pinned Tauri implementation](https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.11.4/crates/tauri-bundler/src/bundle.rs).

The repository-owned installer template is pinned to Tauri CLI 2.11.4, retains
its MIT notice in the packaged resources, and checks all destinations before
`SetOutPath`. This earlier placement matters because `SetOutPath` creates missing
directories. [NSIS reference](https://nsis.sourceforge.io/Reference/SetOutPath).
It also guards optional app-data cleanup and moves dependency bootstrap files
from a shared fixed temp name into the installer's private plugin directory.
These preflight checks do not claim protection against concurrent same-user
filesystem races; no handle-based filesystem transaction was implemented.

Complete component hashes, toolchain/dependency identities, captured shell,
checksum companion, and extracted-payload smoke logs are under
`build/tauri-candidates/3bc04b8d48df46d089db71d1d8919455/`.
The machine-readable `candidate-validation.json` keeps package integrity,
packaged automated smokes, full-suite testing, signing, and live acceptance
separate. It deliberately reports overall release readiness **FAIL**.

## Remaining release work

1. Review the accumulated source changes, establish a clean reviewed commit,
   and build immutable 0.6.4 artifacts with pinned source/tool/dependency
   identities. Do not relabel the existing installer or reuse 0.1.0/0.6.3.
2. Rebuild the frozen components from that reviewed commit, run their actual workflows, and
   compare source build records, staged files, archived/installer payloads,
   checksums, displayed versions and installed identities.
3. Qualify the generated NSIS installer in disposable Windows environments.
   The new guards and packaging capture are implemented and harness-tested;
   exercise the complete installer UI/registration/dependency lifecycle with
   junction canaries for install, repair, upgrade and uninstall, including
   optional app-data deletion. Address positive long-path support or explicitly
   approve a documented installation-path limit before qualification.
4. Verify intended Tauri distribution/update behavior: the current desktop
   update operation is read-only and the legacy feed consumer selects a ZIP,
   while the Tauri build produces NSIS. Do not advertise a complete unattended
   NSIS update/repair/rollback path based on the legacy helper tests.
5. Exercise unavailable WebView2/offline dependencies, fresh-user install,
   spaces/long paths, in-use files, interrupted swaps, failed restart,
   successful rollback, preserved custom files and settings, and uninstall
   with/without explicit data removal. Use disposable canaries throughout.
6. Obtain the missing native symlink-privilege checks and reproducible Tk
   validation environment. Preserve every skip and any intermittent Tk setup
   warning in the evidence; do not relabel them as success.
7. Collect independent live SDK/preview and relevant Reactor Story Mode
   evidence per edition only after separate permission for live testing.
   Include actual loaded dependency identities and per-check observations.
   Pin the authority's session/identity evidence outside the submitted report.

## Evidence locations and commands

Generated evidence is under `build/release-hardening-20260904/`:
`baseline-reproduction.json`, `artifact-identities.json`,
`python-tests.log`, `coverage.json`, `python-tests-final.log`,
`python-tests-final.xml`, `coverage-final.json`, `react-tests-final.log`,
`react-build.log`, `rust-tests-final.log`, `native-rpf-tests.log`, and
`previous-candidate-smoke.log`. The last is explicitly the older frozen build.
RC follow-up evidence includes `candidate-build.log` (failed first attempt),
`candidate-build-final.log` (successful sealed candidate), `python-tests-rc.log`,
`coverage-rc.json`, `python-tests-rc-final.log`, `python-tests-rc-final.xml`, and
`coverage-rc-final.json`. Installed identity auditing remains read-only.

```powershell
python -m pytest --cov=allin1_sdk --cov-report=term
pnpm --dir desktop test
pnpm --dir desktop build
cargo test --manifest-path desktop/src-tauri/Cargo.toml
dotnet run --project tools/RpfPatcher.Tests/RpfPatcher.Tests.csproj -c Release
python scripts/audit_release_state.py
python scripts/qualify_release.py --help
```

Do not run NSIS, modify game installs, or publish a release merely because these
commands pass. The separate untested/failed release gates above remain binding.
