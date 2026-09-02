# Network page roadmap

Status: planned under [Milestone 5](ROADMAP.md#5-relationships-and-research-map).

## Product outcome

The **Network** page lets one researcher inspect how sources already saved in Litrev relate to one
another. It is a research navigation tool, not a citation-count leaderboard or an automatic map of
the entire scholarly web.

The first useful workflow is:

```text
save DOI papers → explicitly look up OpenAlex references → match local DOIs → inspect citation edges
```

Every node is a saved Litrev source. Every edge has a type, direction where applicable, and
inspectable provenance. Opening the page reads local data only; external providers are contacted
only after a visible user action.

## Do we need citations for every paper?

Citation metadata is needed for an edge that claims **paper A cites paper B**, but a complete
citation list is not required before the Network page can exist.

- All saved sources can appear as nodes, including sources with no DOI or no available references.
- A citation edge appears only when Litrev has an explicit citation observation or the user creates
  one manually.
- Fetching each local paper's outgoing references is enough to discover citation edges among local
  sources. A separate global list of incoming citations is not required for this first scope.
- Provider coverage is partial. “No references returned” must never be displayed as “this paper
  cites nothing.”
- Shared authors, tags, collections, or similar text may help filter or group nodes later, but they
  are not citation evidence and must not silently become citation edges.

The recommended first citation provider is OpenAlex. Litrev can resolve a saved canonical DOI with
the singleton Work lookup, validate that the returned DOI is an exact canonical match, and then read
the Work's outgoing `referenced_works`. The referenced Work IDs are resolved in bounded batches so
the source detail view can show the reference list and DOI-bearing targets can be matched to saved
sources. A missing or mismatched Work is an explicit failure; title, author, or year similarity
never substitutes for the DOI lookup.

OpenAlex permits casual keyless API use and offers a free API key with a larger usage budget. Litrev
therefore supports a keyless one-source lookup and an optional locally stored API key, while making
the provider, transmitted DOI, remaining-budget failures, and whole-library request count visible.
Current limits and pricing must be checked against the provider documentation during
implementation rather than encoded from this roadmap.

Crossref remains a possible complementary provider because Litrev already has a Crossref metadata
boundary and some Crossref records contain deposited references. It is not an automatic fallback:
adding a Crossref citation refresh later requires a separately named user action and preserves
provider-specific provenance and coverage. Crossref's complete incoming Cited-by list is a member
service and is not a dependable public boundary for Litrev.

Primary provider references:

- [OpenAlex API reference](https://help.openalex.org/api/)
- [OpenAlex citation API recipes](https://help.openalex.org/how-to/api-recipes/)
- [OpenAlex API authentication](https://help.openalex.org/api/authentication/)
- [OpenAlex API pricing](https://help.openalex.org/access/pricing/)
- [Crossref reference guidance](https://www.crossref.org/documentation/principles-practices/best-practices/references/)
- [Crossref Cited-by documentation](https://www.crossref.org/documentation/cited-by/)
- [Crossref REST API documentation](https://support.crossref.org/hc/en-us/articles/214320426-REST-API)

## Grounded starting point

Litrev already has:

- local source records with canonical DOI handling and preserved standard identifiers;
- explicit Crossref lookup with bounded networking, actionable failures, and provider provenance;
- source search, tags, collections, reading status, and source-detail navigation;
- a first-class sidebar pattern used by Library and Reader; and
- an in-memory `NetworkX.MultiDiGraph` prototype with `cites`, `supports`, `contradicts`, `extends`,
  and `related` kinds.

Litrev does not yet have:

- persisted source-to-source relationships;
- stored citation observations or reference-list coverage state;
- an OpenAlex citation client or API-key setting;
- citation refresh endpoints;
- a network read model in the local API;
- a Network page or accessible relationship list; or
- a verified role for NetworkX beyond the prototype.

Imported bibliography citation keys identify records inside BibTeX, RIS, or CSL JSON. They are not
reference lists and must not be interpreted as citation relationships.

## Decisions for the first release

### Local graph scope

- Nodes are saved Litrev sources, not arbitrary provider works.
- Papers, books, and other source types may appear. Citation refresh is available only when the
  source has a valid DOI; other sources can still participate in manual relationships.
- Isolated nodes remain visible by default so missing provider coverage is not hidden.
- External references are retained as unresolved observations with their OpenAlex Work ID and any
  available DOI and display metadata, but do not become visible nodes until the matching DOI source
  is added to Litrev.
- Deleting a target source makes the provider observation unresolved instead of deleting the
  observation. Re-adding a source with the same canonical DOI can resolve it again.

### Relationship semantics

The initial persisted relationship types are deliberately narrow:

- `cites`: directed from the citing source to the cited source;
- `related`: symmetric and created explicitly by the user.

A user may create a manual `cites` relationship when provider data is unavailable. The UI must
distinguish user-created citation evidence from provider-observed citation evidence.

`supports`, `contradicts`, and `extends` are deferred. Those labels make claims about argument or
evidence and should eventually point to notes, claims, or passages rather than exist as unsupported
source-level assertions.

### Provenance is part of the edge

The graph view merges evidence for the same typed edge but never discards its origins. An edge can
carry one or more provenance entries:

- user-created: creation time and relationship record identity;
- provider-observed: provider, provider record URL, citing DOI, cited DOI when available, first
  seen, last seen, provider Work identities, and active/inactive state.

Selecting an edge opens these details. Provider-observed edges cannot be edited into another type;
manual evidence can be removed without deleting provider evidence for the same edge.

### Explicit networking

- Opening Network performs one local API read and no external request.
- **Look up references with OpenAlex** names OpenAlex and the DOI that will be transmitted.
- Whole-library refresh shows the number of eligible DOI sources before starting and processes one
  source at a time so progress, stopping, rate limiting, and per-source failures remain visible.
- A batch action sequences the per-source endpoint; it does not introduce a background-job system.
- A configured OpenAlex API key is stored behind a dedicated local settings boundary, is never
  returned to the UI after being saved, and is sent only to OpenAlex. Keyless use remains available
  within the provider's current policy.
- Litrev never uploads PDFs, extracted Markdown, notes, tags, or collections for citation lookup.

### Presentation boundaries

- The API returns semantic nodes, edges, provenance, and coverage; it does not return pixel
  coordinates or own the visual layout.
- The React page owns layout and interaction. Layout changes are presentation state, not canonical
  research data.
- A graph-rendering dependency is chosen only after a bounded spike against the acceptance fixture.
  Evaluate bundle size, license, deterministic layout, zoom/pan, directed edges, React integration,
  and maintainability.
- The visual graph is never the only interface. A keyboard-operable relationship list exposes the
  same sources, edge types, direction, and provenance.
- If the persisted design does not use NetworkX for a current domain requirement, remove the
  prototype and dependency instead of retaining two graph representations.

## Authoritative data model

The exact SQL names may change during implementation, but the ownership and guarantees should not.

### Citation refresh

One successful provider retrieval for one citing source records:

- source identity and canonical DOI used for the request;
- provider, provider Work ID, and provider record URL;
- retrieval time;
- the reference-list coverage state;
- total returned Work IDs, resolved references, and DOI-bearing references; and
- the number of DOI observations matched to local sources.

A provider or validation failure does not replace the last successful snapshot or deactivate its
observations.

### Citation observation

One normalized outgoing OpenAlex reference records:

- citing source and canonical citing DOI;
- citing and cited OpenAlex Work IDs;
- canonical cited DOI when OpenAlex supplies one;
- minimal provider display metadata needed for the References list, such as title and year;
- provider provenance;
- first-seen and last-seen times;
- whether it appeared in the latest successful snapshot; and
- an optional matched local target source.

On a successful refresh, observed provider Work IDs are upserted and previous provider observations
not present in the new snapshot become inactive rather than being silently deleted. Automatic local
matching uses a canonical cited DOI only; a Work ID without a DOI remains visible in the source's
References list but cannot create a local citation edge. The default graph shows active matched
observations; the edge inspector can explain inactive history. Invalid or duplicate references are
reported in refresh diagnostics and do not create duplicate observations.

The citing source owns its observations and deletes them when the source is explicitly deleted.
The target match uses `SET NULL` behavior so deleting a cited local source preserves the unresolved
provider observation. If the citing source DOI changes, snapshots fetched for its previous DOI
become stale and stop producing active graph edges; refreshing the new DOI remains an explicit
action. The same edit locally rematches observations from other sources that target the old or new
DOI.

### Manual source relationship

One user-created relationship records:

- source and target source identifiers;
- `cites` or `related` type;
- creation time; and
- user origin.

Self-relationships are rejected. Directed citation uniqueness uses `(source, target, type)`.
Symmetric `related` relationships store endpoints in canonical order so the reverse pair cannot be
duplicated. Source deletion removes owned manual relationships through the existing confirmed
workflow.

### Network read model

`GET /api/network` returns:

- nodes with source ID, type, title, authors, year, reading status, tags, and collections;
- merged typed edges with direction and all provenance entries;
- citation coverage totals: DOI-eligible, refreshed, empty provider reference lists, matched,
  unresolved, and isolated; and
- enough stable identity for the UI to retain selection while filters or layout change.

The response contains local source metadata only. It does not copy complete external provider
records into the UI contract.

## Delivery plan

### N1. Persist trustworthy relationship data

- [ ] Replace the in-memory prototype with forward-only persistence for citation refreshes,
  citation observations, and manual source relationships.
  - Preserve every existing source and identifier through migration from the current schema.
  - Define cascade and `SET NULL` behavior explicitly and test source deletion and rematching.
  - Keep relationship rules independent of FastAPI, React, SQLAlchemy, and graph layout where
    practical.
  - Remove or narrow the existing `RelationshipKind` prototype so there is one authoritative
    vocabulary.
- [ ] Add the local network read boundary.
  - Return all saved sources, including isolates, and merge duplicate edge evidence without losing
    provenance.
  - Add focused indexes for source endpoints, active provider observations, and canonical cited
    DOI matching.
  - Cover empty, isolated, directed, symmetric, duplicate, inactive, and deleted-target cases with
    persistence and API tests.

### N2. Resolve DOI sources and ingest OpenAlex references explicitly

- [ ] Add an OpenAlex citation provider service separate from source metadata proposals.
  - Accept one authoritative canonical DOI and use OpenAlex's singleton Work lookup only after an
    explicit action.
  - Canonicalize and compare the returned `ids.doi` with the requested DOI before trusting the Work
    ID or references. Treat not found and identifier mismatch as distinct failures.
  - Read `referenced_works` and resolve those Work IDs in bounded batches within the provider's
    documented limits, requesting only fields needed for matching, display, and provenance.
  - Bound timeout, response bytes, reference count, batch count, and retry delay. Never fall back to
    Crossref or another provider silently.
  - Distinguish no source DOI, work not found, identifier mismatch, empty references, unresolved
    referenced Work, authentication failure, exhausted usage budget or rate limit, timeout,
    oversized response, and malformed provider data.
- [ ] Add optional OpenAlex API-key configuration.
  - Add a dedicated local settings boundary, store the key locally, redact it from API responses
    and logs, and send it only to OpenAlex.
  - Allow a single keyless lookup while the provider permits it. Explain that a free key increases
    the available budget instead of implying that all usage is unlimited.
- [ ] Add `POST /api/sources/{source_id}/citation-refreshes`.
  - Recheck the source DOI after networking and commit one source's refreshed observation snapshot
    atomically.
  - A failed refresh preserves the previous successful snapshot and every manual relationship.
  - Rematch stored DOI observations when a source is created, imported, deleted, or changes DOI,
    without another external request. Mark that source's outgoing snapshot stale when its DOI no
    longer matches the DOI used for retrieval.
  - Return coverage counts and specific skipped-reference diagnostics without exposing the raw
    provider payload.
- [ ] Prove provider behavior with fixed, non-networked fixtures.
  - Cover exact and mismatched DOI lookup, absent and empty references, DOI and non-DOI referenced
    Works, equivalent DOI forms, duplicates, unresolved Work IDs, response bounds, authentication
    and budget failures, snapshot changes, rollback, and rematching.

### N3. Add Network as a first-class page

- [ ] Add **Network** to the existing sidebar and application-state navigation.
  - Opening it loads only `GET /api/network`; it never refreshes citations implicitly.
  - Provide loading, empty-library, no-relationships, partial-coverage, error, retry, and populated
    states with deliberate focus movement.
  - Selecting a node can open the existing source detail screen and return to the previous Network
    context.
- [ ] Render a focused local-source graph.
  - Show directed citation arrows and visually distinct manual-related edges without relying on
    color alone.
  - Keep labels readable at the default zoom, reveal detail on selection, and avoid rendering every
    metadata field inside a node.
  - Preserve the selected node or edge across deterministic relayout, filtering, and local data
    refresh where it still exists.
  - Add an equivalent relationship list with source titles, direction, edge type, and provenance
    controls for keyboard and screen-reader use.
- [ ] Add explicit citation refresh controls.
  - A source action refreshes one DOI; a Network action visibly sequences all eligible DOI sources.
  - Show OpenAlex and the transmitted DOI before the request. Show progress, current source,
    successful sources, empty reference lists, unresolved Work and DOI counts, failures, provider
    budget state when available, and a stop control between requests.
  - Refresh completion updates the local graph without navigating away or losing the current
    filter and selection.
- [ ] Add the provider reference list to source detail.
  - List the outgoing papers OpenAlex returned, including title, year, DOI when present, refresh
    time, provider link, and whether each reference matches a saved Litrev source.
  - Open a matched local source in Litrev; keep non-DOI and unmatched works visibly external rather
    than hiding them or manufacturing local graph nodes.
  - Provide loading, never-refreshed, empty, partial-resolution, error, and populated states with
    keyboard-operable links and actions.

### N4. Let the user record relationships providers cannot supply

- [ ] Add manual `related` and `cites` relationships from the Network page.
  - Require two distinct saved sources and make citation direction explicit before saving.
  - Show the pending relationship in plain language, such as “A cites B,” before confirmation.
  - Save through `POST /api/source-relationships`; do not let the client manufacture provider
    provenance.
  - Deleting a manual relationship requires confirmation and removes only that manual evidence.
- [ ] Add relationship inspection to source detail.
  - List incoming citations, outgoing citations, and manual relationships using the same network
    read vocabulary.
  - Link every local endpoint back to its source and every provider observation to its provider
    record.

### N5. Make the graph useful rather than decorative

- [ ] Add filters that answer local research questions.
  - Search nodes by saved metadata and filter by citation/manual edges, direction, source type,
    reading status, tag, collection, and isolated status.
  - Allow focusing one source and its one-hop neighborhood before adding deeper traversal.
  - Display counts for visible nodes and edges and state when filters hide isolates or
    relationships.
- [ ] Prove usability and performance at a representative personal-library size.
  - Define a generated fixture before selecting limits; include hundreds of nodes, isolates,
    multiple components, and enough edges to expose layout and interaction problems.
  - Measure initial layout, filter response, selection, and relayout in the production build and
    record the tested fixture size rather than claiming unlimited scale.
  - When density exceeds the useful visual threshold, keep the relationship list usable and offer
    a focused-neighborhood view instead of silently dropping edges.
- [ ] Complete real-boundary verification and documentation.
  - API and persistence tests cover migrations, graph reads, explicit provider refresh, provenance,
    rematching, deletion, uniqueness, partial snapshots, and transaction rollback.
  - React tests cover no implicit networking, all page states, node and edge inspection, citation
    direction, manual creation/removal, filters, keyboard use, batch progress, stopping, and
    provider failures.
  - Run the full Python, React, production-build, and Tauri checks. Interactive visualization QA is
    opt-in and uses an isolated generated library when requested.

## Acceptance path

The first complete vertical slice is:

1. Save or import papers A and B with distinct canonical DOIs.
2. Open Network and see both as local nodes with no external request.
3. Explicitly look up A by DOI in an OpenAlex fixture in which A's `referenced_works` includes B.
4. Inspect A's References list, then reopen Network and inspect the directed `A → B` citation edge
   and OpenAlex provenance.
5. Restart the application and confirm the edge and coverage state remain available locally.
6. Delete B and confirm A's citation observation remains unresolved rather than disappearing.
7. Re-add B with the same DOI and confirm the edge resolves without another provider request.
8. Add and remove a manual relationship and confirm provider evidence is unchanged.

## Acceptance criteria

- Opening Network never contacts an external service.
- All saved sources can be represented, including isolates and sources without DOIs.
- A citation arrow exists only when a directed user assertion or inspectable provider observation
  supports it.
- Missing provider references are described as missing coverage, not as proof that no citations
  exist.
- Every edge exposes type, direction, active state, and provenance.
- Provider refresh is explicit, bounded, transactional per source, and never removes manual data.
- Adding, editing, importing, or deleting a DOI source rematches stored observations without an
  implicit network request; outgoing observations fetched for a previous source DOI become stale.
- The graph and equivalent relationship list support keyboard operation and understandable empty,
  partial, loading, failure, and dense states.
- The original PDFs, extracted text, notes, and metadata remain local and unchanged by graph
  layout or citation refresh.

## Out of scope for the first release

- expanding the graph with papers that are not saved in Litrev;
- background citation synchronization or automatic refresh on page load;
- parsing reference lists from PDFs or Anydoc Markdown;
- title/author fuzzy matching for references without stable identifiers;
- treating citation counts as quality or evidence strength;
- inferred semantic-similarity, co-author, shared-tag, or shared-collection edges;
- author, institution, topic, claim, and evidence nodes;
- `supports`, `contradicts`, or `extends` edges without source-linked evidence;
- multi-hop path analysis, centrality rankings, community detection, or recommendations;
- incoming-citation expansion through OpenAlex's `cites` filter;
- Semantic Scholar, Google Scholar scraping, commercial indexes, or automatic provider fallback
  chains;
- downloading cited papers or attachments; and
- AI-generated relationship labels.

Those capabilities can be reconsidered only after the local, provenance-visible citation and
manual-relationship workflow is dependable.
