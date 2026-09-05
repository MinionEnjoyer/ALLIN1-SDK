# Exact RPF member packages — schema 3

Schema 3 is a deliberately narrow, replacement-only package contract shared by
the SDK and Launcher. Older schema-1/2 readers reject it before installation;
do not change the schema number to work around that refusal. Recipients need a
Launcher built with this contract and the matching exact-path native helper.
Local implementation and testing do not imply an already-published Launcher release.

## Manifest

```toml
schema_version = 3
id = "example.text-patch"
name = "Example text patch"
version = "1.0.0"
author = "Package author"
type = "rpf"
editions = ["enhanced"]
dependencies = ["openrpf"]

[[rpf_entries]]
source = "payload/replacement.gxt2"
archive = "mods/update/text-fixture.rpf"
entry = "text/global.gxt2"
sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
original_sha256 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
```

The hashes above are illustrative, not valid payload evidence. `sha256` is the
replacement file's SHA-256; `original_sha256` is the required existing member's
SHA-256. Both must be lowercase 64-digit hexadecimal strings.

- Exactly one edition: `legacy` or `enhanced`.
- Dependency list is exactly `["openrpf"]`; no loose files, DLC registrations,
  extension tables, or unknown top-level/member fields.
- Between 1 and 128 replacement records. Existing loader rules also reject
  duplicate/conflicting destinations and unsafe payload paths.
- Archive paths are explicit safe GTA-relative `mods/.../*.rpf` paths.
- Member paths are exact files in the outer archive, including ordinary folders.
  They must not contain an archive layer, an `.rpf` path component, or `!`.
- Missing members are refused, not created. Nested members use the separate
  [schema-4 contract](rpf-member-package-v4.md), never a schema-3 extension.

## Installation and restore

All originals are extracted and checksum-checked before the install prepares
game writes. If a mods copy is absent, the corresponding existing vanilla
archive supplies read-only preflight evidence. The usual installer then prepares
the mods copy. Captured original backups are checked again immediately before
replacement, and the applied cache must match the declared payload hash.

Receipts retain schema 3 and the original/payload hashes. Enable/disable verifies
both caches before using them, while rollback independently verifies the original
backup. Current-member ownership checks remain in force. Uninstall an existing
version before installing a replacement version; automatic patch stacking and
rebasing are not part of this format.

Managed reads use `extract-exact-entry`. Missing/failed/older helpers fail closed;
there is no legacy basename or suffix-search fallback. This does not enlarge the
SDK's authority to install runtime prerequisites or bypass closed-game checks.

## Tauri authoring scope

The GXT2 workspace exposes **Selected dictionary only · schema 3** under ALLIN1
export settings for outer-archive workspaces. The reviewed ZIP contains exactly
`mod.toml`, `README.txt`, portable `allin1.rpf-build.json`, and
`payload/replacement.gxt2`. The complete verified RPF build is still required and
revalidated; its archive bytes are not shipped in this mode. The original source
archive/game context is not required merely to publish an already verified build.

The review binds scope, saved workspace, source build, target, original/payload
hashes and compatibility acknowledgment. Switching scope or encountering stale
evidence requires a fresh review and confirmation. Nested workspaces use schema 4 with a separate compatibility
warning. Whole-archive schema-1 export remains a distinct choice,
with its own overwrite-risk acknowledgment; it is never an automatic fallback.

## Remaining RPF migration work

1. Complete advanced lock recovery. Tauri supports revisioned multi-entry change
   sets, plan export, confirmed execution/verification/rollback on explicit external
   or GTA mods copies, bounded receipt history and metadata-only interrupted-receipt
   reconciliation. Stock archives remain blocked. Staging/export never executes a
   plan. Receipt recovery never rewrites archives; a separate reviewed cleanup can
   retain and remove a matching exited-owner lock. General orphan recovery remains.
2. Exercise native dialogs and clean-machine installation. The closed-game
   frozen/native schema-3 fixture test has passed; this does not certify production
   game installations or the installer experience on a clean machine.
3. Certify schema-4 nested-member distribution on large/encrypted production
   archives; the bounded local implementation and fixture coverage are available.
4. Migrate the RPF node graph and program plan/run editor.
5. Validate large/encrypted production archives and release compatible signed
   SDK/Launcher artifacts. New DLC registration is not supplied by this format.

## Reproduction

```powershell
python -m pytest tests/test_rpf_member_contract.py tests/test_rpf_member_publication.py tests/test_rpf_package_publication.py -q
python scripts/smoke_desktop_sidecar.py desktop/src-tauri/sidecar/ALLIN1-SDK-Desktop-Sidecar.exe --resource-home desktop/src-tauri/standalone-resources --rpf-game-path '<GTA context>' --rpf-launcher-source '../ALLIN1/src'
```

The optional native smoke generates its own OPEN RPFs and isolated fake game
tree. Real GTA is only read-only decoding context; no real install targets are
used. It checks root/nested copy builds, exact-member ZIP content, native
install/disable/enable/uninstall, preservation of the unrelated nested dictionary,
and rejection of a mismatched original. GTA must be closed for the packaged
authoring safety gate. Local tests cover both sibling repositories when present;
SDK-only CI explicitly skips sibling-Launcher cases.
