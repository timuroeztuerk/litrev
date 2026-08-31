# Opt-in audit backlog

This file records findings from a code-quality audit. It is not the product roadmap, does not set
task priority, and must not expand the scope of unrelated work.

Agents should read or act on this file only when the user explicitly asks to consult `TODO.md`,
review the audit backlog, or work on an item recorded here. Otherwise, ignore it. Before acting on
an item, reproduce it against the current code because concurrent or later work may already have
changed the relevant behavior.

## Confirmed issues at the time of the audit

Each issue should be handled as its own bounded change with focused regression coverage.

- [ ] Reject a stale DOI metadata review after the source DOI changes.
  - The apply endpoint checked selected metadata fields but did not verify that the source's
    current DOI still matched the lookup's requested and retrieved DOI.
  - The audit reproduced a source with DOI `10.1234/second` receiving metadata and provenance from
    a review for `10.1234/first`.
  - Acceptance: changing the DOI after lookup makes the old review inapplicable and leaves the
    source and provenance unchanged.
- [ ] Enforce one canonical DOI identity across manual capture, editing, and bibliography import.
  - Manual create and update trimmed DOI text but relied on exact SQLite string uniqueness, while
    bibliography import used the canonical DOI helper.
  - The audit reproduced two successful source creations for `10.1234/Example` and
    `https://doi.org/10.1234/example`.
  - Acceptance: equivalent resolver prefixes and case variants resolve to one source identity at
    every write boundary, including concurrent writes and migration of existing records.
- [ ] Preserve UTC semantics in API timestamps.
  - UTC datetimes were persisted through timezone-naive SQLite `DateTime` columns and serialized
    without `Z` or an offset. Browsers therefore interpreted them as local wall-clock values.
  - This affects displayed DOI provenance times and makes the API timestamp contract ambiguous.
  - Acceptance: API timestamps include an explicit UTC offset, existing persisted values retain
    their intended UTC meaning, and frontend formatting is covered outside UTC.

## Cleanup candidates

- [ ] Remove the unused `.input-row` CSS rule and its related combined and responsive selectors
  after confirming no active UI branch or concurrent work uses that class.
- [ ] Remove the unused `ConvertedDocument.filename` field, or document and test a current caller
  that needs it.
- [ ] Decide whether the currently unused `thumbnails` library directory is intentional near-term
  storage or premature scaffolding. Keep it if the reader design requires it; otherwise defer its
  creation until there is a real owner.

## Audit verification context

At the time of the audit, 145 Python tests and 45 frontend tests passed, along with frontend lint
and build, Ruff checks, and Rust `cargo check`. Those checks did not cover the three confirmed
contract issues above.
