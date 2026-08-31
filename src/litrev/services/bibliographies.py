from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import rispy
from pybtex.database import BibliographyData, Entry, Person, parse_string
from pybtex.exceptions import PybtexError
from pybtex.richtext import Text

from litrev.domain.sources import SourceType

MAX_BIBLIOGRAPHY_BYTES = 5 * 1024 * 1024
MAX_BIBLIOGRAPHY_ENTRIES = 5_000
_RIS_LIST_TAGS = [*rispy.LIST_TYPE_TAGS, "SN", "AN"]
_BIBTEX_IDENTIFIER_FIELD = "litrev_identifiers"
_CSL_IDENTIFIER_FIELD = "litrev-identifiers"
_RIS_IDENTIFIER_PREFIX = "litrev-id:"


class BibliographyFormat(StrEnum):
    BIBTEX = "bibtex"
    RIS = "ris"
    CSL_JSON = "csl-json"


class BibliographyImportError(ValueError):
    pass


class UnsupportedBibliographyFormatError(BibliographyImportError):
    pass


class MalformedBibliographyError(BibliographyImportError):
    pass


class EmptyBibliographyError(BibliographyImportError):
    pass


class BibliographyEntryLimitError(BibliographyImportError):
    pass


class BibliographyExportError(ValueError):
    pass


class EmptyBibliographyExportError(BibliographyExportError):
    pass


class BibliographySerializationError(BibliographyExportError):
    pass


@dataclass(frozen=True)
class BibliographyIdentifier:
    identifier_type: str
    value: str


@dataclass(frozen=True)
class BibliographyCitationKey:
    bibliography_format: BibliographyFormat
    value: str


@dataclass(frozen=True)
class BibliographyExportSource:
    source_id: int
    source_type: SourceType
    title: str
    authors: list[str]
    publication_year: int | None
    venue: str | None
    doi: str | None
    url: str | None
    abstract: str | None
    language: str | None
    identifiers: list[BibliographyIdentifier]
    citation_keys: list[BibliographyCitationKey]


@dataclass(frozen=True)
class BibliographySourceDraft:
    entry_id: str
    citation_key: str | None
    source_type: SourceType
    title: str
    authors: list[str]
    publication_year: int | None
    venue: str | None
    doi: str | None
    url: str | None
    abstract: str | None
    language: str | None
    identifiers: list[BibliographyIdentifier]


@dataclass(frozen=True)
class ParsedBibliography:
    bibliography_format: BibliographyFormat
    sources: list[BibliographySourceDraft]


def serialize_bibliography(
    sources: Sequence[BibliographyExportSource],
    bibliography_format: BibliographyFormat,
) -> str:
    ordered_sources = sorted(
        sources,
        key=lambda source: (source.title.casefold(), source.title, source.source_id),
    )
    if not ordered_sources:
        raise EmptyBibliographyExportError("The library has no sources to export.")

    record_keys = _record_keys(ordered_sources, bibliography_format)
    if bibliography_format is BibliographyFormat.BIBTEX:
        return _serialize_bibtex(ordered_sources, record_keys)
    if bibliography_format is BibliographyFormat.RIS:
        return _serialize_ris(ordered_sources, record_keys)
    return _serialize_csl_json(ordered_sources, record_keys)


class _UnnumberedRisWriter(rispy.RisWriter):
    def set_header(self, count: int) -> str:
        return ""


def _record_keys(
    sources: Sequence[BibliographyExportSource],
    bibliography_format: BibliographyFormat,
) -> dict[int, str]:
    keys: dict[int, str] = {}
    used: set[str] = set()

    for source in sources:
        candidate = next(
            (
                key.value
                for key in source.citation_keys
                if key.bibliography_format is bibliography_format
            ),
            None,
        )
        if candidate is None or not _safe_record_key(candidate, bibliography_format):
            continue
        identity = candidate.casefold()
        if identity not in used:
            keys[source.source_id] = candidate
            used.add(identity)

    for source in sources:
        if source.source_id in keys:
            continue
        base = f"litrev-{source.source_id}"
        candidate = base
        suffix = 2
        while candidate.casefold() in used:
            candidate = f"{base}-{suffix}"
            suffix += 1
        keys[source.source_id] = candidate
        used.add(candidate.casefold())

    return keys


def _safe_record_key(value: str, bibliography_format: BibliographyFormat) -> bool:
    if not value or value != value.strip():
        return False
    if bibliography_format is BibliographyFormat.BIBTEX:
        return re.fullmatch(r"[A-Za-z0-9_:.+/-]+", value) is not None
    return not any(character in "\r\n" or ord(character) < 32 for character in value)


def _serialize_bibtex(
    sources: Sequence[BibliographyExportSource],
    record_keys: Mapping[int, str],
) -> str:
    entries: dict[str, Entry] = {}
    for source in sources:
        fields = {"title": _bibtex_field(source.title)}
        _add_optional_fields(
            fields,
            year=str(source.publication_year) if source.publication_year is not None else None,
            doi=_optional_bibtex_field(source.doi),
            url=_optional_bibtex_field(source.url),
            abstract=_optional_bibtex_field(source.abstract),
            language=_optional_bibtex_field(source.language),
        )
        if source.venue:
            fields["journal" if source.source_type is SourceType.PAPER else "publisher"] = (
                _bibtex_field(source.venue)
            )

        direct_identifiers, extra_identifiers = _partition_identifiers(
            source.identifiers,
            {"isbn", "issn", "pmid", "pmcid", "arxiv"},
        )
        for identifier_type in ("isbn", "issn", "pmid", "pmcid"):
            identifier = direct_identifiers.get(identifier_type)
            if identifier is not None:
                fields[identifier_type] = _bibtex_field(identifier.value)
        arxiv_identifier = direct_identifiers.get("arxiv")
        if arxiv_identifier is not None:
            fields["archiveprefix"] = "arXiv"
            fields["eprint"] = _bibtex_field(arxiv_identifier.value)
        if extra_identifiers:
            fields[_BIBTEX_IDENTIFIER_FIELD] = json.dumps(
                [_identifier_object(identifier) for identifier in extra_identifiers],
                ensure_ascii=False,
                separators=(",", ":"),
            )

        persons = (
            {"author": [_literal_bibtex_person(author) for author in source.authors]}
            if source.authors
            else {}
        )
        entries[record_keys[source.source_id]] = Entry(
            _bibtex_export_type(source.source_type),
            fields=fields,
            persons=persons,
        )

    try:
        return BibliographyData(entries=entries).to_string("bibtex", encoding="UTF-8")
    except (PybtexError, UnicodeError) as error:
        raise BibliographySerializationError(
            "The library contains text that cannot be represented safely as BibTeX."
        ) from error


def _serialize_ris(
    sources: Sequence[BibliographyExportSource],
    record_keys: Mapping[int, str],
) -> str:
    records: list[dict[str, object]] = []
    for source in sources:
        record: dict[str, object] = {
            "type_of_reference": _ris_export_type(source.source_type),
            "id": record_keys[source.source_id],
            "title": source.title,
        }
        _add_optional_fields(
            record,
            authors=source.authors or None,
            year=str(source.publication_year) if source.publication_year is not None else None,
            doi=source.doi,
            urls=[source.url] if source.url else None,
            abstract=source.abstract,
            language=source.language,
        )
        if source.venue:
            record["journal_name" if source.source_type is SourceType.PAPER else "publisher"] = (
                source.venue
            )

        serial_type = "isbn" if source.source_type is SourceType.BOOK else "issn"
        serials: list[str] = []
        accession_numbers: list[str] = []
        for identifier in _ordered_identifiers(source.identifiers):
            if identifier.identifier_type == serial_type:
                serials.append(identifier.value)
            elif identifier.identifier_type == "accession" and not identifier.value.startswith(
                _RIS_IDENTIFIER_PREFIX
            ):
                accession_numbers.append(identifier.value)
            else:
                encoded_identifier = json.dumps(
                    _identifier_object(identifier),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                accession_numbers.append(f"{_RIS_IDENTIFIER_PREFIX}{encoded_identifier}")
        _add_optional_fields(
            record,
            issn=serials or None,
            accession_number=accession_numbers or None,
        )
        records.append(record)

    try:
        return rispy.dumps(
            records,
            implementation=_UnnumberedRisWriter,
            list_tags=_RIS_LIST_TAGS,
        )
    except (TypeError, ValueError) as error:
        raise BibliographySerializationError(
            "The library contains text that cannot be represented safely as RIS."
        ) from error


def _serialize_csl_json(
    sources: Sequence[BibliographyExportSource],
    record_keys: Mapping[int, str],
) -> str:
    entries: list[dict[str, object]] = []
    for source in sources:
        entry: dict[str, object] = {
            "id": record_keys[source.source_id],
            "type": _csl_export_type(source.source_type),
            "title": source.title,
        }
        _add_optional_fields(
            entry,
            author=[{"literal": author} for author in source.authors] or None,
            issued={"date-parts": [[source.publication_year]]}
            if source.publication_year is not None
            else None,
            DOI=source.doi,
            URL=source.url,
            abstract=source.abstract,
            language=source.language,
        )
        if source.venue:
            entry["container-title" if source.source_type is SourceType.PAPER else "publisher"] = (
                source.venue
            )

        direct_identifiers, extra_identifiers = _partition_identifiers(
            source.identifiers,
            {"isbn", "issn", "pmid", "pmcid", "arxiv"},
        )
        for identifier_type, field in (
            ("isbn", "ISBN"),
            ("issn", "ISSN"),
            ("pmid", "PMID"),
            ("pmcid", "PMCID"),
        ):
            identifier = direct_identifiers.get(identifier_type)
            if identifier is not None:
                entry[field] = identifier.value
        arxiv_identifier = direct_identifiers.get("arxiv")
        if arxiv_identifier is not None:
            entry["archive"] = "arXiv"
            entry["archive_location"] = arxiv_identifier.value
        if extra_identifiers:
            entry["custom"] = {
                _CSL_IDENTIFIER_FIELD: [
                    _identifier_object(identifier) for identifier in extra_identifiers
                ]
            }
        entries.append(entry)

    return f"{json.dumps(entries, ensure_ascii=False, indent=2)}\n"


def _add_optional_fields(values: dict[str, object], **fields: object) -> None:
    values.update((key, value) for key, value in fields.items() if value is not None)


def _partition_identifiers(
    identifiers: Sequence[BibliographyIdentifier],
    supported_types: set[str],
) -> tuple[dict[str, BibliographyIdentifier], list[BibliographyIdentifier]]:
    direct: dict[str, BibliographyIdentifier] = {}
    extra: list[BibliographyIdentifier] = []
    for identifier in _ordered_identifiers(identifiers):
        if (
            identifier.identifier_type in supported_types
            and identifier.identifier_type not in direct
        ):
            direct[identifier.identifier_type] = identifier
        else:
            extra.append(identifier)
    return direct, extra


def _ordered_identifiers(
    identifiers: Sequence[BibliographyIdentifier],
) -> list[BibliographyIdentifier]:
    return sorted(
        identifiers,
        key=lambda identifier: (
            identifier.identifier_type,
            identifier.value.casefold(),
            identifier.value,
        ),
    )


def _identifier_object(identifier: BibliographyIdentifier) -> dict[str, str]:
    return {"type": identifier.identifier_type, "value": identifier.value}


def _bibtex_field(value: str) -> str:
    return value.replace("{", r"\{").replace("}", r"\}")


def _optional_bibtex_field(value: str | None) -> str | None:
    return _bibtex_field(value) if value is not None else None


def _literal_bibtex_person(author: str) -> Person:
    try:
        return Person(f"{{{_bibtex_field(author)}}}")
    except (PybtexError, ValueError) as error:
        raise BibliographySerializationError(
            "The library contains an author that cannot be represented safely as BibTeX."
        ) from error


def _bibtex_export_type(source_type: SourceType) -> str:
    if source_type is SourceType.PAPER:
        return "article"
    if source_type is SourceType.BOOK:
        return "book"
    return "misc"


def _ris_export_type(source_type: SourceType) -> str:
    if source_type is SourceType.PAPER:
        return "JOUR"
    if source_type is SourceType.BOOK:
        return "BOOK"
    return "GEN"


def _csl_export_type(source_type: SourceType) -> str:
    if source_type is SourceType.PAPER:
        return "article-journal"
    if source_type is SourceType.BOOK:
        return "book"
    return "document"


def parse_bibliography(data: bytes, filename: str) -> ParsedBibliography:
    bibliography_format = _format_from_filename(filename)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise MalformedBibliographyError(
            "Bibliography files must use UTF-8 text encoding."
        ) from error

    if not text.strip():
        raise EmptyBibliographyError("The bibliography file is empty.")

    if bibliography_format is BibliographyFormat.BIBTEX:
        sources = _parse_bibtex(text)
    elif bibliography_format is BibliographyFormat.RIS:
        sources = _parse_ris(text)
    else:
        sources = _parse_csl_json(text)

    if not sources:
        raise EmptyBibliographyError("The bibliography file does not contain any sources.")
    if len(sources) > MAX_BIBLIOGRAPHY_ENTRIES:
        raise BibliographyEntryLimitError(
            f"Bibliography imports are limited to {MAX_BIBLIOGRAPHY_ENTRIES:,} sources."
        )
    return ParsedBibliography(bibliography_format=bibliography_format, sources=sources)


def normalize_imported_doi(doi: str) -> str:
    value = doi.strip()
    lowered = value.casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if lowered.startswith(prefix):
            value = value[len(prefix) :].strip()
            break
    return value


def doi_key(doi: str) -> str:
    return normalize_imported_doi(doi).casefold()


def _format_from_filename(filename: str) -> BibliographyFormat:
    suffix = Path(filename).suffix.casefold()
    if suffix == ".bib":
        return BibliographyFormat.BIBTEX
    if suffix == ".ris":
        return BibliographyFormat.RIS
    if suffix == ".json":
        return BibliographyFormat.CSL_JSON
    raise UnsupportedBibliographyFormatError(
        "Choose a BibTeX (.bib), RIS (.ris), or CSL JSON (.json) file."
    )


def _parse_bibtex(text: str) -> list[BibliographySourceDraft]:
    try:
        bibliography = parse_string(text, "bibtex")
        sources: list[BibliographySourceDraft] = []
        for entry_id, entry in bibliography.entries.items():
            fields = entry.fields
            authors = [_bibtex_person(person) for person in entry.persons.get("author", [])]
            sources.append(
                BibliographySourceDraft(
                    entry_id=entry_id,
                    citation_key=entry_id,
                    source_type=_bibtex_source_type(entry.type),
                    title=_bibtex_text(fields.get("title", "")),
                    authors=authors,
                    publication_year=_year_from(fields.get("year")),
                    venue=_first_value(
                        fields,
                        "journal",
                        "booktitle",
                        "publisher",
                        "school",
                        "institution",
                        transform=_bibtex_text,
                    ),
                    doi=_optional_bibtex_text(fields.get("doi")),
                    url=_optional_bibtex_text(fields.get("url")),
                    abstract=_optional_bibtex_text(fields.get("abstract")),
                    language=_optional_bibtex_text(fields.get("language")),
                    identifiers=_bibtex_identifiers(fields),
                )
            )
        return sources
    except (PybtexError, UnicodeError) as error:
        raise MalformedBibliographyError("The BibTeX file could not be parsed.") from error


def _parse_ris(text: str) -> list[BibliographySourceDraft]:
    start_count = len(re.findall(r"^TY  -\s*\S+", text, flags=re.MULTILINE))
    end_count = len(re.findall(r"^ER  -\s*$", text, flags=re.MULTILINE))
    if start_count == 0 or start_count != end_count:
        raise MalformedBibliographyError("The RIS file has an incomplete reference record.")

    entries = rispy.RisParser(
        list_tags=_RIS_LIST_TAGS,
    ).parse(text)
    if len(entries) != start_count:
        raise MalformedBibliographyError("The RIS file could not be parsed completely.")

    sources: list[BibliographySourceDraft] = []
    for index, entry in enumerate(entries, start=1):
        citation_key = _optional_record_key(entry.get("id"))
        entry_id = citation_key or f"record {index}"
        authors = _string_list(entry.get("authors") or entry.get("first_authors"), entry_id)
        source_type = _ris_source_type(_optional_string(entry.get("type_of_reference")))
        sources.append(
            BibliographySourceDraft(
                entry_id=entry_id,
                citation_key=citation_key,
                source_type=source_type,
                title=(
                    _optional_string(entry.get("title"))
                    or _optional_string(entry.get("primary_title"))
                    or ""
                ),
                authors=authors,
                publication_year=_year_from(
                    _optional_string(entry.get("year"))
                    or _optional_string(entry.get("publication_year"))
                ),
                venue=(
                    _optional_string(entry.get("journal_name"))
                    or _optional_string(entry.get("secondary_title"))
                    or _optional_string(entry.get("publisher"))
                ),
                doi=_optional_string(entry.get("doi")),
                url=_first_string(entry.get("urls"), entry_id),
                abstract=(
                    _optional_string(entry.get("abstract"))
                    or _optional_string(entry.get("notes_abstract"))
                ),
                language=_optional_string(entry.get("language")),
                identifiers=_ris_identifiers(entry, source_type, entry_id),
            )
        )
    return sources


def _parse_csl_json(text: str) -> list[BibliographySourceDraft]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise MalformedBibliographyError("The CSL JSON file could not be parsed.") from error

    if isinstance(value, dict):
        entries = [value]
    elif isinstance(value, list):
        entries = value
    else:
        raise MalformedBibliographyError("CSL JSON must contain a source or a list of sources.")

    sources: list[BibliographySourceDraft] = []
    for index, raw_entry in enumerate(entries, start=1):
        if not isinstance(raw_entry, dict):
            raise MalformedBibliographyError(f"CSL JSON source {index} must be an object.")
        entry = raw_entry
        citation_key = _optional_record_key(entry.get("id"))
        entry_id = citation_key or f"source {index}"
        sources.append(
            BibliographySourceDraft(
                entry_id=entry_id,
                citation_key=citation_key,
                source_type=_csl_source_type(_optional_string(entry.get("type"))),
                title=_optional_string(entry.get("title")) or "",
                authors=_csl_authors(entry.get("author"), entry_id),
                publication_year=_csl_year(entry.get("issued"), entry_id),
                venue=_optional_string(entry.get("container-title"))
                or _optional_string(entry.get("publisher")),
                doi=_optional_string(entry.get("DOI")),
                url=_optional_string(entry.get("URL")),
                abstract=_optional_string(entry.get("abstract")),
                language=_optional_string(entry.get("language")),
                identifiers=_csl_identifiers(entry),
            )
        )
    return sources


def _bibtex_text(value: str) -> str:
    marker_index = 0
    while True:
        left_marker = f"\ufdd0litrev-left-brace-{marker_index}\ufdef"
        right_marker = f"\ufdd0litrev-right-brace-{marker_index}\ufdef"
        if left_marker not in value and right_marker not in value:
            break
        marker_index += 1
    protected = value.replace(r"\{", left_marker).replace(r"\}", right_marker)
    return (
        Text.from_latex(protected)
        .render_as("plaintext")
        .replace(left_marker, "{")
        .replace(right_marker, "}")
    )


def _optional_bibtex_text(value: str | None) -> str | None:
    return _bibtex_text(value) if value is not None else None


def _bibtex_person(person: Person) -> str:
    given = [*person.first_names, *person.middle_names]
    family = [*person.prelast_names, *person.last_names]
    name = _bibtex_text(" ".join([*given, *family]))
    if person.lineage_names:
        name = f"{name}, {_bibtex_text(' '.join(person.lineage_names))}"
    return name


def _first_value(
    values: Mapping[str, str],
    *keys: str,
    transform: Callable[[str], str],
) -> str | None:
    for key in keys:
        if key in values:
            return transform(values[key])
    return None


def _bibtex_identifiers(fields: Mapping[str, str]) -> list[BibliographyIdentifier]:
    identifiers = [
        BibliographyIdentifier(identifier_type=identifier_type, value=_bibtex_text(fields[field]))
        for field, identifier_type in (
            ("isbn", "isbn"),
            ("issn", "issn"),
            ("pmid", "pmid"),
            ("pmcid", "pmcid"),
            ("arxiv", "arxiv"),
        )
        if field in fields
    ]
    archive_prefix = fields.get("archiveprefix")
    eprint = fields.get("eprint")
    if archive_prefix is not None and archive_prefix.strip().casefold() == "arxiv" and eprint:
        identifiers.append(
            BibliographyIdentifier(identifier_type="arxiv", value=_bibtex_text(eprint))
        )
    extension = fields.get(_BIBTEX_IDENTIFIER_FIELD)
    if extension is not None:
        try:
            extension_value = json.loads(extension)
        except json.JSONDecodeError as error:
            raise MalformedBibliographyError(
                "A Litrev BibTeX identifier extension is invalid."
            ) from error
        identifiers.extend(_identifier_extension(extension_value, "BibTeX"))
    return identifiers


def _ris_identifiers(
    entry: Mapping[str, object],
    source_type: SourceType,
    entry_id: str,
) -> list[BibliographyIdentifier]:
    serial_type = "isbn" if source_type is SourceType.BOOK else "issn"
    identifiers = [
        BibliographyIdentifier(identifier_type=serial_type, value=value)
        for value in _string_values(entry.get("issn"), entry_id, "issn")
    ]
    for value in _string_values(entry.get("accession_number"), entry_id, "accession_number"):
        if value.startswith(_RIS_IDENTIFIER_PREFIX):
            try:
                extension_value = json.loads(value[len(_RIS_IDENTIFIER_PREFIX) :])
            except json.JSONDecodeError:
                extension_value = None
            if (
                isinstance(extension_value, dict)
                and isinstance(extension_value.get("type"), str)
                and isinstance(extension_value.get("value"), str)
            ):
                identifiers.append(
                    BibliographyIdentifier(
                        identifier_type=extension_value["type"],
                        value=extension_value["value"],
                    )
                )
                continue
        identifiers.append(BibliographyIdentifier(identifier_type="accession", value=value))
    return identifiers


def _csl_identifiers(entry: Mapping[str, object]) -> list[BibliographyIdentifier]:
    identifiers: list[BibliographyIdentifier] = []
    for field, identifier_type in (
        ("ISBN", "isbn"),
        ("ISSN", "issn"),
        ("PMID", "pmid"),
        ("PMCID", "pmcid"),
    ):
        value = _optional_string(entry.get(field))
        if value is not None:
            identifiers.append(BibliographyIdentifier(identifier_type=identifier_type, value=value))
    archive = _optional_string(entry.get("archive"))
    archive_location = _optional_string(entry.get("archive_location"))
    if archive is not None and archive.strip().casefold() == "arxiv" and archive_location:
        identifiers.append(BibliographyIdentifier(identifier_type="arxiv", value=archive_location))
    custom = entry.get("custom")
    if isinstance(custom, dict) and _CSL_IDENTIFIER_FIELD in custom:
        identifiers.extend(_identifier_extension(custom[_CSL_IDENTIFIER_FIELD], "CSL JSON"))
    return identifiers


def _identifier_extension(value: object, format_name: str) -> list[BibliographyIdentifier]:
    if not isinstance(value, list):
        raise MalformedBibliographyError(f"A Litrev {format_name} identifier extension is invalid.")

    identifiers: list[BibliographyIdentifier] = []
    for item in value:
        if not isinstance(item, dict):
            raise MalformedBibliographyError(
                f"A Litrev {format_name} identifier extension is invalid."
            )
        identifier_type = item.get("type")
        identifier_value = item.get("value")
        if not isinstance(identifier_type, str) or not isinstance(identifier_value, str):
            raise MalformedBibliographyError(
                f"A Litrev {format_name} identifier extension is invalid."
            )
        identifiers.append(
            BibliographyIdentifier(
                identifier_type=identifier_type,
                value=identifier_value,
            )
        )
    return identifiers


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MalformedBibliographyError("Bibliography metadata fields must contain text.")
    return value


def _optional_record_key(value: object) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return _optional_string(value)


def _string_list(value: object, entry_id: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise MalformedBibliographyError(f'Source "{entry_id}" has an invalid author list.')
    return value


def _string_values(value: object, entry_id: str, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise MalformedBibliographyError(
        f'Source "{entry_id}" has invalid {field.replace("_", " ")} identifiers.'
    )


def _first_string(value: object, entry_id: str) -> str | None:
    values = _string_list(value, entry_id)
    return values[0] if values else None


def _year_from(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"(?<!\d)(\d{4})(?!\d)", value)
    return int(match.group(1)) if match else None


def _csl_authors(value: object, entry_id: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise MalformedBibliographyError(f'Source "{entry_id}" has an invalid author list.')

    authors: list[str] = []
    for author in value:
        if not isinstance(author, dict):
            raise MalformedBibliographyError(f'Source "{entry_id}" has an invalid author.')
        literal = _optional_string(author.get("literal"))
        if literal is not None:
            authors.append(literal)
            continue
        parts = [
            _optional_string(author.get("given")),
            _optional_string(author.get("non-dropping-particle")),
            _optional_string(author.get("family")),
        ]
        name = " ".join(part for part in parts if part)
        suffix = _optional_string(author.get("suffix"))
        if suffix:
            name = f"{name}, {suffix}" if name else suffix
        authors.append(name)
    return authors


def _csl_year(value: object, entry_id: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise MalformedBibliographyError(f'Source "{entry_id}" has an invalid issued date.')
    date_parts = value.get("date-parts")
    if date_parts is None:
        return _year_from(
            _optional_string(value.get("raw")) or _optional_string(value.get("literal"))
        )
    if (
        not isinstance(date_parts, list)
        or not date_parts
        or not isinstance(date_parts[0], list)
        or not date_parts[0]
        or not isinstance(date_parts[0][0], int)
    ):
        raise MalformedBibliographyError(f'Source "{entry_id}" has an invalid issued date.')
    return date_parts[0][0]


def _bibtex_source_type(entry_type: str) -> SourceType:
    if entry_type.casefold() in {
        "article",
        "conference",
        "inproceedings",
        "mastersthesis",
        "phdthesis",
        "techreport",
        "unpublished",
    }:
        return SourceType.PAPER
    if entry_type.casefold() in {"book", "booklet", "inbook", "incollection", "manual"}:
        return SourceType.BOOK
    return SourceType.OTHER


def _ris_source_type(entry_type: str | None) -> SourceType:
    if entry_type in {"JOUR", "JFULL", "EJOUR", "CONF", "CPAPER", "THES", "RPRT", "UNPB"}:
        return SourceType.PAPER
    if entry_type in {"BOOK", "EBOOK", "CHAP", "ECHAP"}:
        return SourceType.BOOK
    return SourceType.OTHER


def _csl_source_type(entry_type: str | None) -> SourceType:
    if entry_type in {
        "article",
        "article-journal",
        "article-magazine",
        "article-newspaper",
        "manuscript",
        "paper-conference",
        "report",
        "thesis",
    }:
        return SourceType.PAPER
    if entry_type in {"book", "chapter", "entry-dictionary", "entry-encyclopedia"}:
        return SourceType.BOOK
    return SourceType.OTHER
