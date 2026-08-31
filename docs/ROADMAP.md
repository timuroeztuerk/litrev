# Litrev roadmap

Litrev should help one researcher move safely from a collected document to source-linked notes,
evidence, relationships, and a literature-review outline. Every derived idea should remain
traceable to its origin.

## How to use this roadmap

- Milestones are ordered by product dependency, not by estimated duration.
- Work should normally start with the first unchecked item in the current milestone.
- Each change should deliver one observable workflow and its important failure states.
- Automated tests, static checks, and builds are the default acceptance evidence; interactive
  browser QA is performed only when the user specifically requests it.
- `[ ]` means planned; `[-]` means intentionally deferred.

The current milestone is **2.1. DOI-first source capture**. Milestone 3 follows it.

## Completed baseline: foundation and durable ingestion

Milestones 0 and 1 established the development stack and the first dependable vertical slice:

- React, TypeScript, Vite, and Tauri interface with a local FastAPI service;
- SQLite/SQLAlchemy storage with forward-only Alembic migrations and isolated test libraries;
- manual capture of books and papers;
- confirmed document import into a managed local library;
- attachment records with checksums, duplicate detection, conversion state, and diagnostics;
- persisted Anydoc Markdown with visible, retryable failure states;
- multiple distinct attachments per source; and
- confirmed removal of failed attachments with database and filesystem safeguards.

The completed workflow is:

```text
select document → inspect → confirm source → save original → extract → reopen later
```

Verified guarantees include migration from the pre-Alembic schema, persistence across restarts,
duplicate-byte detection, a 50 MB import limit, explicit unsupported/encrypted/malformed/OCR-needed
states, recovery from storage or database failures, and protection of pending or successful
attachments from deletion.

## 2. Useful library and metadata

Outcome: a growing research library can be found, corrected, organized, imported, and exported.

Manual title-and-type capture already exists.

- [x] Add authors, year, venue, DOI, URL, abstract, language, and reading status.
- [x] Add source editing with explicit validation and conflict behavior.
- [x] Add source-list search, sorting, and filtering across saved metadata.
- [x] Add tags and collections for persistent organization.
- [x] Add explicit source deletion with attachment cleanup safeguards.
- [x] Import BibTeX, RIS, and CSL JSON.
- [x] Export standard bibliographic formats without losing identifiers.
- [x] Add opt-in DOI metadata lookup with provenance and conflict review.

Metadata is stored locally and can be edited from the source detail screen. Empty optional values
are normalized consistently, URLs must use HTTP or HTTPS, and duplicate DOI changes are rejected
without partially changing the source.

Library discovery now provides case-insensitive search across title, authors, venue, and DOI;
sorting by title, publication year, or date added; and combinable source-type and reading-status
filters. It covers saved source metadata only. Search within extracted text belongs to milestone 6.
Tags and collections are reusable, case-insensitive names assigned from the source editor and can
be combined with the other library filters. Source deletion now requires confirmation, removes
source-owned database relationships, stages every managed original and extraction before the
database commit, and restores staged files when the commit fails. Shared tag and collection
definitions remain available for other sources. Bibliography import now accepts UTF-8 BibTeX, RIS,
and CSL JSON files, validates the complete import before saving, maps supported metadata into the
source model, preserves supported non-DOI identifiers and format-scoped record keys, and reports
DOI duplicates without overwriting saved sources. Preserved identifiers can be corrected from the
source detail screen. Bibliography export downloads the full library as deterministic UTF-8
BibTeX, RIS, or CSL JSON, reuses safe format-scoped record keys, generates stable keys for manual
sources, and preserves standard and explicitly named identifiers across Litrev round trips. The
DOI workflow contacts Crossref only after an explicit lookup action, presents current and proposed
values side by side, leaves conflicting fields unselected, and applies only the fields the user
chooses. Saved identifiers are merged rather than replaced. Applied fields retain the provider,
provider record link, DOI, and retrieval and application times on the source detail screen.

Milestone 2 is complete. A bounded DOI-first capture follow-up comes next, before milestone 3.

Acceptance criteria:

- A user can import an existing bibliography, find a source, correct its metadata, and export it
  without losing identifiers.
- External metadata never overwrites user edits without confirmation.

## 2.1. DOI-first source capture

Outcome: a user can enter one DOI, review the matching provider metadata, and create a source
without inventing a temporary title or saving a placeholder record.

This slice reuses the completed Crossref client, canonical metadata proposal, identifier handling,
and provenance model. It must not introduce a second DOI parser, a parallel metadata model, or an
automatic lookup on page load.

- [ ] Add a source-independent DOI preview endpoint.
  - Accept one DOI, normalize it through the authoritative DOI boundary, and reject empty or
    unusable input before networking.
  - Check the library for an existing canonical DOI before contacting Crossref. Return the existing
    source identity so the UI can open it instead of creating or overwriting anything.
  - Return the same canonical provider proposal used by source enrichment, plus provider identity,
    record URL, retrieval time, and a deterministic proposal fingerprint. Do not create a source or
    provenance row during preview.
- [ ] Add a transactional create-from-DOI endpoint.
  - Require the normalized DOI, reviewed proposal fingerprint, and the fields explicitly selected
    by the user. DOI and a non-empty provider title are mandatory. Use the existing provider type
    mapping when available and fall back to `other`; other available fields are optional.
  - Re-fetch and validate the provider proposal before creation so an untrusted or stale client
    cannot manufacture Crossref provenance. If the fingerprint changed, return the new review
    instead of applying unseen metadata.
  - Create the source and its applied provenance in one database transaction. A provider,
    validation, uniqueness, or commit failure must leave no partial source or lookup record.
  - Recheck DOI uniqueness at creation time and return a specific existing-source conflict for the
    race between preview and confirmation.
- [ ] Add the DOI-first capture workflow to the existing capture area.
  - Let the user choose title-based capture or DOI-based capture without adding a separate generic
    dashboard or duplicating the existing source editor.
  - Make both outbound actions visible: **Look up DOI** retrieves the preview and **Add source**
    revalidates it before saving.
  - Show the normalized DOI, provider link, mapped source type, title, authors, year, venue, URL,
    abstract, language, and identifiers that are available. Keep the required title selected and
    allow the user to exclude optional fields.
  - On success, open the newly created source. If the DOI already exists, offer to open that source.
  - Cover initial, invalid-input, loading, review, changed-provider-data, duplicate, service-error,
    cancellation, and success states with labels, keyboard access, and deliberate focus movement.
- [ ] Prove the workflow through its real boundaries.
  - API tests cover DOI normalization, duplicate detection without a provider call, missing titles,
    provider 404/rate-limit/timeout/malformed responses, changed proposal fingerprints, selected
    fields, identifier deduplication, transaction rollback, and the preview-to-create race.
  - React tests prove there is no request before an explicit action, cancellation saves nothing,
    the reviewed selection is sent, duplicates open the existing source, failures remain
    actionable, and successful creation opens the persisted source.
  - The acceptance path is: DOI → preview → review → create → reopen → export → import into an empty
    library → compare canonical metadata and supported identifiers. Provenance is verified on the
    created source because bibliography formats do not carry Litrev's provider audit record.

Acceptance criteria:

- Given only a DOI whose provider record has a usable title, a user can create and reopen a source
  without entering placeholder metadata.
- No source is stored before confirmation, and every failure or cancellation leaves the library
  unchanged.
- The created source contains exactly the required DOI and title plus the optional fields the user
  selected, with inspectable Crossref provenance.
- A DOI already in the library is opened, never duplicated or overwritten.

Out of scope for this slice: batch DOI capture, automatic enrichment, alternate metadata providers,
attachment discovery or download, and citation/reference ingestion.

## 3. Reader, locators, and annotations

Outcome: a user can read a document and create notes that return to an exact location.

- [ ] Evaluate visual PDF renderers separately from Anydoc; record the decision and license.
- [ ] Add page navigation, zoom, text selection, and in-document search.
- [ ] Store page-aware highlights and annotations with stable locators and selected text.
- [ ] Link annotations to notes and reopen the relevant location.
- [ ] Define fallback locators for non-PDF formats.

Acceptance criteria:

- Clicking a note locator reopens the correct source and location.
- Annotations survive upgrades and do not rely only on transient DOM geometry.

## 4. Notes and synthesis workbench

Outcome: reading artifacts become a structured, reviewable body of evidence.

- [ ] Add free-form and structured paper summaries.
- [ ] Add atomic notes linked to one or more sources or annotations.
- [ ] Add claims with supporting, contradicting, and contextual evidence.
- [ ] Add research questions, review projects, and an evidence matrix.
- [ ] Add backlinks and orphan-note detection.
- [ ] Export a project outline as Markdown with source references.

Acceptance criteria:

- A claim exposes every supporting or contradicting source passage.
- A project export preserves source identity and locators.

## 5. Relationships and research map

Outcome: meaningful relationships among sources, authors, concepts, and claims are inspectable.

- [ ] Replace the in-memory graph prototype with persisted typed relationships.
- [ ] Distinguish user-created, imported citation, and inferred links.
- [ ] Add a focused graph UI with filtering and useful default layouts.
- [ ] Ingest citations and references where reliable metadata is available.
- [ ] Add author, topic, and timeline views only when their underlying data is inspectable.

Acceptance criteria:

- Every graph edge exposes its type and provenance.
- The graph remains usable for a realistically sized personal library.

## 6. Full-text search and retrieval

Outcome: a user can retrieve passages and ideas across the library, not only source metadata.

- [ ] Index source metadata, Anydoc Markdown, notes, and annotations with SQLite FTS5.
- [ ] Return contextual snippets linked to their source locations.
- [ ] Add saved searches and project-scoped search.
- [ ] Evaluate optional local embeddings only after lexical search is dependable.

Acceptance criteria:

- Results explain why they matched and open the underlying source or note.
- Rebuilding the index never changes canonical research data.

## 7. Packaging, portability, and resilience

Outcome: Litrev behaves like a dependable personal desktop application.

- [ ] Package the Python service as a managed Tauri sidecar.
- [ ] Add clean startup, shutdown, health recovery, and port-conflict handling.
- [ ] Produce installable Linux builds, then evaluate macOS and Windows.
- [ ] Add library backup, restore, integrity checks, and a human-readable export.
- [ ] Harden crash recovery across imports and database/file transactions.
- [ ] Test upgrades using a library from the previous release.

Acceptance criteria:

- The packaged application starts without separately installed Python or Node.js.
- A user can back up, restore, and inspect the library outside Litrev.

## 8. Optional AI assistance

Outcome: AI accelerates synthesis without weakening traceability or creating provider dependence.

- [ ] Define provider-neutral interfaces and a fully functional no-AI mode.
- [ ] Add library-grounded question answering with source and locator citations.
- [ ] Suggest structured summaries that require user review.
- [ ] Extract evidence with visible provenance and confidence boundaries.
- [ ] Make local versus remote processing and transmitted data explicit before enabling a provider.

Acceptance criteria:

- Generated statements link to inspectable evidence.
- Disabling or changing the provider does not make existing research inaccessible.

## Deferred until justified

- [-] Multi-user collaboration and shared libraries
- [-] Cloud synchronization
- [-] Mobile applications
- [-] Plugin marketplace
- [-] Automated writing without source review
