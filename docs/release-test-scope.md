# Public release-test scope

The candidate gate runs every test discoverable by the normal Python and React
commands except the four exact entries in `release-test-scope.json`. They require
private retail bytes or an explicitly opted-in private suppressor fixture that is
not available to public CI. It records a reason for every excluded node or file.
The file is an exact allow-list: candidate tooling
rejects any added, removed, renamed, or reclassified entry and records its hash
in both the candidate identity and Python/React gate evidence.

All in-scope public tests must pass without skips, todo, or pending outcomes.
The excluded checks are recorded as **NOT TESTED**, never as passed, waived, or
part of a full-suite claim. This does not establish retail compatibility, live
game behavior, or private-fixture parity.
