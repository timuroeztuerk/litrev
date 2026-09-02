from dataclasses import dataclass
from typing import Literal

from litrev.domain.sources import SourceType

MetadataIdentifierType = Literal["doi", "isbn"]


@dataclass(frozen=True)
class MetadataIdentifier:
    identifier_type: str
    value: str


@dataclass(frozen=True)
class MetadataProposal:
    source_type: SourceType | None
    title: str | None
    authors: list[str] | None
    publication_year: int | None
    venue: str | None
    url: str | None
    abstract: str | None
    language: str | None
    identifiers: list[MetadataIdentifier] | None


@dataclass(frozen=True)
class RetrievedMetadata:
    provider: str
    provider_url: str
    identifier_type: MetadataIdentifierType
    retrieved_identifier: str
    proposal: MetadataProposal
