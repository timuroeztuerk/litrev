from __future__ import annotations

from enum import StrEnum

import networkx as nx


class RelationshipKind(StrEnum):
    CITES = "cites"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    EXTENDS = "extends"
    RELATED = "related"


class ResearchGraph:
    """In-memory graph of relationships between research objects."""

    def __init__(self) -> None:
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()

    def connect(self, source_id: str, target_id: str, kind: RelationshipKind) -> None:
        self._graph.add_edge(source_id, target_id, kind=kind)

    def relationships(self, source_id: str, target_id: str) -> set[RelationshipKind]:
        edges = self._graph.get_edge_data(source_id, target_id, default={})
        return {edge["kind"] for edge in edges.values()}
