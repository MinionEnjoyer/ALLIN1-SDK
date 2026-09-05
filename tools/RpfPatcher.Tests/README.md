# Exact RPF member regression checks

Run the game-independent checks with:

```powershell
dotnet run --project tools/RpfPatcher.Tests/RpfPatcher.Tests.csproj -c Release
```

The desktop build runs these 51 checks automatically before staging resources.
They use a synthetic CodeWalker directory tree, deliberately misleading display
paths, duplicate/suffix filenames, type collisions and malformed paths. They
require no game, encryption keys, native test framework, or network service.

For real OPEN RPF extraction/mutation plus an optional temporary Launcher
install/disable/enable/uninstall cycle:

```powershell
python scripts/smoke_rpf_exact_entries.py --patcher <RpfPatcher.exe> --game-context <GTA-directory> --batch --launcher-source <Launcher-src-directory>
```

Omit `--batch` for the Launcher helper, which has no SDK batch command. The game
directory supplies read-only edition/key context. All archive and lifecycle
writes occur in an owned temporary directory; the generated fake game uses a
non-executable marker and does not load OpenRPF. Both existing-member replacement
and missing-root addition are checked against same-named decoys elsewhere.
This is not a clean-machine or real-game installation test.

Managed RPF operations require `extract-exact-entry`. Do not add a fallback to
`extract-entry`: its basename-search behavior remains for legacy inventory
callers and is not suitable for member backup/restore. Updating the Launcher
Python code requires shipping its matching rebuilt native helper. Old Launcher
releases are not made safe merely by creating a new SDK export; member-only
distribution needs an enforceable compatibility boundary before exposure.
