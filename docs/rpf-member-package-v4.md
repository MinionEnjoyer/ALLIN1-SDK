# Exact nested RPF member packages — schema 4

Schema 4 extends the *workflow*, not the schema-3 envelope. Both current source
readers support it; older schema-1/2/3 readers reject the version. A compatible
Launcher and its matching native helper must be released together. Do not downgrade
the manifest or claim that an existing published Launcher already supports it.

## Manifest and identity

All schema-3 member-only restrictions still apply: exactly one edition,
OpenRPF dependency, 1–128 records, original and replacement SHA-256, no loose files,
extension tables or DLC registrations, and a safe explicit outer `mods/.../*.rpf`.

```toml
schema_version = 4
id = "example.nested-text"
name = "Nested text patch"
version = "1.0.0"
author = "Package author"
type = "rpf"
editions = ["enhanced"]
dependencies = ["openrpf"]

[[rpf_entries]]
source = "payload/replacement.gxt2"
archive = "mods/update/update.rpf"
entry = "x64/data/lang/american_rel.rpf!global.gxt2"
sha256 = "<actual replacement SHA-256>"
original_sha256 = "<actual required original SHA-256>"
```

Replace the placeholders with real lowercase 64-digit hashes. `!` selects the next
archive, not a directory. There must be 1–8 archive layers followed by a non-RPF
file, at most 2,048 characters total. Every layer and leaf uses an exact safe
relative path. The native resolver walks each selected parent entry by identity;
same-name files elsewhere, display-path aliases and ambiguous names cannot match.
Root dictionaries remain schema 3. Schema 4 does not add/remove archives or files.

## Installation, disable, enable and uninstall

Every original is read and checked before preparing game writes. A missing mods
copy may use the corresponding stock archive as **read-only** preflight evidence.
Receipts retain the complete target chain and both hashes. Original backups and
applied caches contain the leaf only. Cache integrity and current ownership are
checked before use; packages owning the same leaf, a containing RPF member or the
whole outer archive conflict in both directions.

Nested reads use `extract-exact-nested-entry`. Writes use the new
`replace-exact-nested-entry` command with expected-current and replacement hashes.
A missing/old helper fails closed; no legacy extraction, implicit archive traversal,
member creation or whole-archive package fallback is permitted.

For each replacement the native helper requires GTA closed, rejects reparse paths,
takes an exclusive cooperative lock, holds the original read-only with write/delete
sharing denied during staging, and:

1. Copies the outer archive into a unique sibling staging folder.
2. Extracts each containing archive by exact identity to a detached copy.
3. Changes the innermost leaf and rebuilds its parents bottom-up.
4. Reopens the result and verifies every bounded file payload, including unchanged
   neighbours and nested files, against the pre-write archive.
5. Flushes the staged result, rechecks game/path gates, releases the original handle
   and atomically replaces the outer file while retaining the cooperative lock.

The final handle release/replace boundary is not a system-wide transaction against
arbitrary external tools. Never edit the target with another tool during an install.
No crash-journal/resume UI or transactional multi-package install is promised here.
Normal failures before commit preserve the original; idempotent recovery accepts
the already-desired leaf. Install failure recovery continues to use Launcher receipts
and cached originals.

Restore repeats this process against the **current** archive and merges only the
backed-up leaf. A later edit to a neighbouring dictionary is preserved. It never
restores an old containing-RPF blob over unrelated changes. Uninstall before upgrading;
patch stacking/rebasing is not part of this format.

Initial native bounds: outer archive ≤2 GiB; detached/rebuilt child ≤512 MiB;
each decoded file verified ≤128 MiB; 25,000 file fingerprints; 8 nested layers.
Empty/unreadable files, unreadable child archives and insufficient staging space
are refused. These are explicit initial implementation limits, not production
certification for every game archive. Larger/encrypted archives remain validation work.

## Tauri authoring

The saved GXT2 workspace offers **Selected dictionary only · schema 4** for nested
bindings. Review binds the exact chain, original/payload hashes, metadata, source
build and compatibility acknowledgment. It still requires a verified complete RPF
copy build, but ZIP contents remain just `mod.toml`, `README.txt`,
`allin1.rpf-build.json` and `payload/replacement.gxt2`.
No game or Launcher installation occurs during export. Changing scope or stale
evidence requires a fresh review. Whole-archive schema-1 export stays separate.

## Local reproduction

```powershell
python -m pytest tests/test_rpf_nested_member_contract.py tests/test_rpf_member_contract.py tests/test_rpf_member_publication.py tests/test_rpf_package_publication.py -q
dotnet run --project tools/RpfPatcher.Tests/RpfPatcher.Tests.csproj -c Release
python scripts/smoke_rpf_nested_members.py --patcher '<matching RpfPatcher.exe>' --game-context '<read-only GTA context>'
python scripts/smoke_desktop_sidecar.py desktop/src-tauri/sidecar/ALLIN1-SDK-Desktop-Sidecar.exe --resource-home desktop/src-tauri/standalone-resources --rpf-game-path '<read-only GTA context>' --rpf-launcher-source '../ALLIN1/src'
```

Native smokes generate their own temporary archives/fake game trees. They do not
install into real GTA. Do not restage resources while frozen/native tests use them.
