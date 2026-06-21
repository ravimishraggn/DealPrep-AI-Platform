"""GraphIndexer — build and persist the tenant knowledge graph in Neo4j."""
from __future__ import annotations

import logging

from pipeline.contracts import Chunk
from pipeline.indexing.graph.entities import EntityExtractor
from pipeline.indexing.graph.models import Entity, Relationship
from pipeline.indexing.graph.neo4j_client import Neo4jClient
from pipeline.indexing.graph.relationships import RelationshipExtractor
from pipeline.indexing.graph.resolver import EntityResolver

logger = logging.getLogger(__name__)


class GraphIndexer:
    """Extracts entities + relationships from chunks and writes them to Neo4j.

    Every node and edge is tagged with ``tenant_id`` (isolation), plus ``source_id``
    and ``original_file_reference`` (traceability). All writes go through the
    shared, tenant-filtered ``Neo4jClient``.
    """

    def __init__(
        self,
        entity_extractor: EntityExtractor | None = None,
        relationship_extractor: RelationshipExtractor | None = None,
        resolver: EntityResolver | None = None,
        client: Neo4jClient | None = None,
    ) -> None:
        """Wire the graph sub-components (defaults are constructed if omitted)."""
        self.entities = entity_extractor or EntityExtractor()
        self.relationships = relationship_extractor or RelationshipExtractor()
        self.resolver = resolver or EntityResolver()
        self.client = client or Neo4jClient()

    def extract_from_chunks(
        self, chunks: list[Chunk]
    ) -> tuple[list[Entity], list[Relationship]]:
        """Run entity + relationship extraction over chunks (no DB writes).

        Exposed separately so extraction can be tested in isolation before wiring
        into Neo4j.

        Returns:
            ``(entities, relationships)`` de-duplicated across the chunk set.
        """
        all_entities: dict[tuple[str, str], Entity] = {}
        all_rels: list[Relationship] = []
        for chunk in chunks:
            ents = self.entities.extract(chunk.text)
            for e in ents:
                all_entities[(e.norm, e.type)] = e
            all_rels.extend(self.relationships.extract(chunk.text, ents))
        # De-dupe relationship triples.
        seen: set[tuple[str, str, str]] = set()
        unique_rels: list[Relationship] = []
        for r in all_rels:
            key = (r.subject, r.relationship, r.object)
            if key not in seen:
                seen.add(key)
                unique_rels.append(r)
        return list(all_entities.values()), unique_rels

    def index_entities_and_relationships(
        self,
        entities: list[Entity],
        relationships: list[Relationship],
        tenant_id: str,
        source_id: str,
        original_file_reference: str,
    ) -> dict[str, int]:
        """Resolve entities and write nodes + edges to Neo4j for one tenant.

        Args:
            entities: Entities to upsert as nodes.
            relationships: Triples to upsert as edges.
            tenant_id: REQUIRED — stamped on every node and edge for isolation.
            source_id: Originating source (traceability).
            original_file_reference: Source document reference (traceability).

        Returns:
            Counts ``{"entities": n, "relationships": m}`` actually written.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for graph indexing")
        self.client.ensure_constraints()

        # Resolve against existing tenant nodes, then upsert.
        existing = self.client.fetch_entity_names(tenant_id)
        canonical: dict[str, str] = {}
        for entity in entities:
            name = self.resolver.resolve(entity, existing)
            canonical[entity.name] = name
            existing[entity.norm] = name  # subsequent entities can resolve to this one
            self.client.upsert_entity(tenant_id, name, entity.type, source_id, original_file_reference)

        written_rels = 0
        for rel in relationships:
            subj = canonical.get(rel.subject, rel.subject)
            obj = canonical.get(rel.object, rel.object)
            self.client.upsert_relationship(
                tenant_id, subj, rel.relationship, obj, source_id, original_file_reference
            )
            written_rels += 1

        logger.info(
            "graph-indexed %d entities, %d relationships for tenant %s",
            len(canonical), written_rels, tenant_id,
        )
        return {"entities": len(canonical), "relationships": written_rels}

    def index_chunks(
        self, chunks: list[Chunk], tenant_id: str, source_id: str
    ) -> dict[str, int]:
        """Full path: extract from chunks then index into Neo4j.

        Uses the first chunk's ``original_file_reference`` for traceability when
        the batch shares a source document.
        """
        if not chunks:
            return {"entities": 0, "relationships": 0}
        entities, relationships = self.extract_from_chunks(chunks)
        file_ref = chunks[0].original_file_reference
        return self.index_entities_and_relationships(
            entities, relationships, tenant_id, source_id, file_ref
        )
