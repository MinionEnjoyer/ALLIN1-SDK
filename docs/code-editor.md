# XML and Lua source editor

Open **Data Tools → XML & Lua editor** in the React/Tauri v2 SDK.
Choose a text `.xml`, `.meta` or `.lua` file, or create a new XML/Lua document.
The editor provides line numbers, syntax highlighting, search/replace, and
Ctrl+Z/Ctrl+Y undo/redo. Tab remains available for keyboard focus navigation.

1. Edit your source. Unsaved work blocks workspace switching and window closure.
2. Use **Check syntax**. XML checks well-formedness without loading DTDs or
   entities. Lua checks the Lua 5.4 grammar without executing the file.
3. **Review save** shows a unified diff, exact output hash and backup path.
   Check the confirmation box before applying. Returning to the draft cancels
   that review; a later save needs fresh confirmation.
4. **Review save a copy** asks for a folder and creates a new filename. It never
   overwrites an existing destination. Sources inside GTA can only be copied to
   an output outside the installation.

Saving an existing file retains its previous bytes beside it as
`.filename.<SHA-256>.allin1-backup`. Retain these files for recovery. To recover,
copy the desired backup to a new file with the original extension and inspect
it before replacing anything. In-session undo is independent of disk backups.
If another program changes the source after opening/review, the SDK refuses the
save and retains your draft. Copy that draft elsewhere or explicitly close it
before reopening the external revision.

## Limits and release evidence

- UTF-8 only, including an optional retained UTF-8 BOM; consistent LF or CRLF
  endings. Maximum 64 KiB and 2,000 lines. Larger inputs fail explicitly, never
  silently truncate. The diff preview can be abbreviated, but the entire draft
  is bound to its review hash.
- Compiled META/PSO, binary Lua, UTF-16 files and mixed line endings are not
  raw-text editor inputs. Use the native export/rebuild workflows as appropriate.
- XML diagnostics do not validate a GTA schema. Lua diagnostics do not validate
  game natives, dependencies, security, runtime behavior or FiveM-specific
  extensions such as backtick hashes. No script runner, debugger or Lua runtime
  installation is included.
- Syntax errors block saving; malformed source can still be opened and repaired.
  Editing a checksum-owned package payload does not regenerate its manifest:
  rebuild and validate the package through its authoring workflow afterwards.
- Real-Python React tests cover XML save/backup, Lua copy publication, malformed
  drafts and stale saves. These are not packaged native-dialog or live-game
  acceptance. See the [release guide](release-0.6.4.md).

The editor uses [CodeMirror 6](https://codemirror.net/) with its XML language
support and Lua mode. The Python syntax checker uses
[luaparser](https://github.com/boolangery/py-lua-parser), pinned in `pyproject.toml`.
Frontend versions are pinned in `desktop/package.json` and `pnpm-lock.yaml`.
See the [editor dependency notices](code-editor-notices.md).
