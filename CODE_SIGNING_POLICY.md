# Code signing policy

## Status

The maintainer has selected **unsigned manual-download distribution for 0.6.4**.
The release is still unreleased and not release-qualified. A SignPath certificate
is not part of the near-term release plan; no approval, sponsorship or delivery
date is claimed. No replacement signing provider is promised either.

The older Azure signed-release job is disabled and retained only as a future
integration reference. Neither an unset certificate nor that disabled job blocks
preparing an explicitly unsigned manual package. Tests, reviewed source,
packaged lifecycle, artifact identity and acceptance requirements still apply.
See [release preparation](RELEASE_SIGNING.md).

Windows publisher certificates and automatic-update signatures are separate
trust mechanisms. Unsigned manual downloads do not permit bypassing update
verification. React update installation stays disabled until its own trusted
key/metadata workflow is implemented and qualified.

## Project and release scope

- Source repository: <https://github.com/MinionEnjoyer/ALLIN1-SDK>
- License: GNU General Public License v3.0 or later
- Official downloads: <https://github.com/MinionEnjoyer/ALLIN1-SDK/releases>
- Future signing scope: the SDK desktop executable, structured Agent API executable,
  archive helper, and their open-source runtime dependencies produced by the tagged
  GitHub Actions release workflow.

Third-party mods, imported packages, game files, user projects, local development
builds, and binaries not produced by the repository's verified release workflow are
not eligible for the project's public signature.

## Team roles

- Committer and reviewer: [MinionEnjoyer](https://github.com/MinionEnjoyer)
- Release approver: [MinionEnjoyer](https://github.com/MinionEnjoyer)

Changes proposed by people without repository commit access require review by the
maintainer before merge. Accounts with repository or signing access must use
multi-factor authentication. The release approver is responsible for confirming that
the source revision, version, test result, dependency revision, artifact inventory,
and release notes are correct before approving a signing request.

## Release integrity controls

1. Official release artifacts originate from a versioned tag in this repository.
2. The Windows workflow checks out the pinned submodule revision, installs declared
   dependencies, runs the complete automated test suite, and builds the desktop,
   agent, and helper applications on a clean hosted runner.
3. Inventory the exact shell, sidecar, helpers, resources and third-party payloads.
   Record their actual signature status; do not label them publisher-signed merely
   because a dependency already has a signature.
4. The manual release notes and download instructions must disclose that the
   project release is unsigned. Checksums establish byte consistency, not
   publisher authentication or safety. Do not advise disabling security protections.
5. Generate internal and external SHA-256 checksums from the final bytes. Publish
   those manifests and the build identity alongside the manually approved assets.

Any future signed channel must fail closed on missing or invalid signatures.
The unsigned policy does not relax origin verification, executable inventory,
containment, tests, acceptance or checksums, and does not turn candidates into
approved releases. Never reuse an existing version to conceal changed payloads.

## Privacy and network behavior

ALLIN1 SDK processes selected mods, game installations, archives, metadata, and user
projects locally. It does not automatically transmit files, filenames, paths,
diagnostics, telemetry, analytics, or usage data to the project maintainers or another
network service. The structured Agent API uses local standard input/output and writes
its audit record to the current user's local application-data directory.

The interface can open a public project-support URL only when the user explicitly
selects that link. Windows then hands the URL to the user's default browser, whose own
privacy policy and configuration apply. File-preview actions that use a browser open
local `file:` resources rather than uploading them.

If signing is introduced later, it sends the automated artifact and build-origin
evidence to the selected signing service; that provider's policies would apply.
Explicit update checks contact the release service. React signed update
installation remains disabled pending production identity and metadata; legacy
updater services and user-managed installation are separate workflows.

Optional assistant requests can send the selected prompt and grounded context to
the user-configured compatible API, or to a local model runtime. They are explicit
actions, not background analytics. Saving assistant settings does not start
inference. API keys are referenced by environment-variable name, never written
into SDK settings. Review context and provider privacy before sending private data.

## Reporting concerns

Report a suspected compromised release, incorrect signature, provenance failure, or
privacy issue through the repository's GitHub issue tracker. Do not include private
game files, credentials, signing tokens, or other secrets in a public report.
