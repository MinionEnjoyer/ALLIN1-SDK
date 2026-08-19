# Code signing policy

## Status

ALLIN1 SDK has applied for the SignPath Foundation open-source code-signing
program. Until approval and verified-build integration are complete, public release
files are unsigned and must be verified against the SHA-256 manifests attached to
their GitHub release.

After approval, official signed releases will state:

> Free code signing provided by SignPath.io, certificate by SignPath Foundation.

No project member receives or stores the SignPath Foundation private key.

## Project and release scope

- Source repository: <https://github.com/MinionEnjoyer/ALLIN1-SDK>
- License: GNU General Public License v3.0 or later
- Official downloads: <https://github.com/MinionEnjoyer/ALLIN1-SDK/releases>
- Eligible artifacts: the SDK desktop executable, structured Agent API executable,
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

## Build and signing controls

1. Official release artifacts originate from a versioned tag in this repository.
2. The Windows workflow checks out the pinned submodule revision, installs declared
   dependencies, runs the complete automated test suite, and builds the desktop,
   agent, and helper applications on a clean hosted runner.
3. The signing integration must verify the GitHub build origin and inventory every
   executable `.exe`, `.dll`, and `.pyd` payload before signing.
4. Packaging is permitted only after every executable payload has a valid,
   timestamped Authenticode signature.
5. The package builder creates internal SHA-256 checksums and a separate release
   archive checksum after signing. GitHub Releases publishes those manifests beside
   the archive.

Signing must fail closed. Maintainers must not bypass origin verification, executable
inventory, signature verification, tests, or checksum generation to publish a release.

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

Release signing sends only the automated release artifact and build-origin evidence
to the configured signing service. SignPath's policies apply to that service-side
processing. The SDK does not include an automatic updater; downloading or installing
an SDK release is a separate, explicit action owned by ALLIN1 Launcher or the user.

## Reporting concerns

Report a suspected compromised release, incorrect signature, provenance failure, or
privacy issue through the repository's GitHub issue tracker. Do not include private
game files, credentials, signing tokens, or other secrets in a public report.
