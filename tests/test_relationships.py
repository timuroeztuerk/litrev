from litrev.domain.relationships import RelationshipKind, ResearchGraph


def test_research_objects_can_have_typed_relationships() -> None:
    graph = ResearchGraph()

    graph.connect("paper:1", "paper:2", RelationshipKind.SUPPORTS)
    graph.connect("paper:1", "paper:2", RelationshipKind.EXTENDS)

    assert graph.relationships("paper:1", "paper:2") == {
        RelationshipKind.SUPPORTS,
        RelationshipKind.EXTENDS,
    }
