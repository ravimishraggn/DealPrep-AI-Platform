"""Shared, tenant-filtered Neo4j access (ADR 0006).

THE single place Cypher is written. Every public method takes ``tenant_id`` as a
required argument and every query hard-codes a ``{tenant_id: $tenant_id}`` filter
on nodes and relationships, so tenant isolation cannot be accidentally skipped —
there is no generic "run arbitrary Cypher" escape hatch.
"""
from __future__ import annotations

import logging
import threading

from app.config import settings

logger = logging.getLogger(__name__)

_driver = None
_driver_lock = threading.Lock()


def get_driver():
    """Return a process-wide cached Neo4j driver built from settings."""
    global _driver
    if _driver is None:
        with _driver_lock:
            if _driver is None:
                from neo4j import GraphDatabase

                _driver = GraphDatabase.driver(
                    settings.neo4j_uri,
                    auth=(settings.neo4j_user, settings.neo4j_password),
                )
    return _driver


def close_driver() -> None:
    """Close the cached driver (called on app shutdown)."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def _require_tenant(tenant_id: str) -> None:
    """Raise unless a non-empty tenant_id is supplied (isolation guard)."""
    if not tenant_id:
        raise ValueError("tenant_id is required for every graph operation")


class Neo4jClient:
    """Tenant-scoped Neo4j operations. All Cypher lives here and is tenant-filtered."""

    def ensure_constraints(self) -> None:
        """Create the uniqueness constraint backing entity MERGE (idempotent).

        Identity is (tenant_id, name) — NOT including type — so an entity whose
        NER label is inconsistent across chunks (e.g. ORG vs PERSON) is still a
        single node. Without this, name-based relationship MATCHes fan out across
        duplicate nodes and create duplicate edges.
        """
        with get_driver().session() as session:
            session.run(
                "CREATE CONSTRAINT entity_tenant_name IF NOT EXISTS "
                "FOR (e:Entity) REQUIRE (e.tenant_id, e.name) IS UNIQUE"
            )

    def upsert_entity(
        self, tenant_id: str, name: str, etype: str, source_id: str, file_ref: str
    ) -> None:
        """MERGE an entity node (identity = tenant_id + name), tagged for trace."""
        _require_tenant(tenant_id)
        with get_driver().session() as session:
            session.run(
                """
                MERGE (e:Entity {tenant_id: $tenant_id, name: $name})
                ON CREATE SET e.type = $etype,
                              e.source_id = $source_id,
                              e.original_file_reference = $file_ref,
                              e.created_at = timestamp()
                """,
                tenant_id=tenant_id, name=name, etype=etype,
                source_id=source_id, file_ref=file_ref,
            )

    def upsert_relationship(
        self, tenant_id: str, subject: str, rel_type: str, obj: str,
        source_id: str, file_ref: str,
    ) -> None:
        """MERGE a directed :RELATED edge (typed by property) between two nodes.

        Uses a single ``RELATED`` edge label with a ``type`` property (Neo4j
        Community lacks APOC dynamic types). Both endpoint matches and the edge
        carry ``tenant_id`` so the whole triple is tenant-isolated.
        """
        _require_tenant(tenant_id)
        with get_driver().session() as session:
            session.run(
                """
                MATCH (s:Entity {tenant_id: $tenant_id, name: $subject})
                MATCH (o:Entity {tenant_id: $tenant_id, name: $obj})
                MERGE (s)-[r:RELATED {tenant_id: $tenant_id, type: $rel_type}]->(o)
                ON CREATE SET r.source_id = $source_id,
                              r.original_file_reference = $file_ref,
                              r.created_at = timestamp()
                """,
                tenant_id=tenant_id, subject=subject, obj=obj, rel_type=rel_type,
                source_id=source_id, file_ref=file_ref,
            )

    def fetch_entity_names(self, tenant_id: str) -> dict[str, str]:
        """Return ``{normalized_name: display_name}`` for a tenant's entities.

        Used by EntityResolver to dedupe new entities against existing nodes.
        """
        _require_tenant(tenant_id)
        from pipeline.indexing.graph.models import normalize_name

        with get_driver().session() as session:
            result = session.run(
                "MATCH (e:Entity {tenant_id: $tenant_id}) RETURN e.name AS name",
                tenant_id=tenant_id,
            )
            return {normalize_name(rec["name"]): rec["name"] for rec in result}

    def find_relationships(self, tenant_id: str, name: str, limit: int = 25) -> list[dict]:
        """Return 1-hop relationships for an entity, tenant-filtered.

        Args:
            tenant_id: REQUIRED tenant filter.
            name: Entity display name to look up (case-insensitive).
            limit: Max edges to return.

        Returns:
            Dicts of ``subject``, ``relationship``, ``object``, ``object_type``,
            and source traceability for each direct relationship.
        """
        _require_tenant(tenant_id)
        with get_driver().session() as session:
            result = session.run(
                """
                MATCH (e:Entity {tenant_id: $tenant_id})-[r:RELATED {tenant_id: $tenant_id}]->(o:Entity {tenant_id: $tenant_id})
                WHERE toLower(e.name) = toLower($name)
                RETURN DISTINCT e.name AS subject, r.type AS relationship, o.name AS object,
                       o.type AS object_type, r.original_file_reference AS file_ref,
                       r.source_id AS source_id
                LIMIT $limit
                """,
                tenant_id=tenant_id, name=name, limit=limit,
            )
            return [dict(rec) for rec in result]

    def list_graph(self, tenant_id: str, limit: int = 100) -> dict:
        """Return a tenant's entities and relationships for the "show data" UI.

        Args:
            tenant_id: REQUIRED tenant filter.
            limit: Max entities and max relationships to return.

        Returns:
            ``{entities: [{name, type}], relationships: [{subject, relationship,
            object, file_ref}]}`` — all tenant-filtered.
        """
        _require_tenant(tenant_id)
        with get_driver().session() as session:
            ents = session.run(
                "MATCH (e:Entity {tenant_id: $tenant_id}) "
                "RETURN e.name AS name, e.type AS type ORDER BY e.name LIMIT $limit",
                tenant_id=tenant_id, limit=limit,
            )
            entities = [dict(r) for r in ents]
            rels = session.run(
                """
                MATCH (a:Entity {tenant_id: $tenant_id})-[r:RELATED {tenant_id: $tenant_id}]->(b:Entity {tenant_id: $tenant_id})
                RETURN a.name AS subject, r.type AS relationship, b.name AS object,
                       r.original_file_reference AS file_ref
                ORDER BY a.name LIMIT $limit
                """,
                tenant_id=tenant_id, limit=limit,
            )
            relationships = [dict(r) for r in rels]
        return {"entities": entities, "relationships": relationships}

    def list_entities(self, tenant_id: str, limit: int = 200) -> list[str]:
        """Return entity display names for a tenant (used to detect query mentions)."""
        _require_tenant(tenant_id)
        with get_driver().session() as session:
            result = session.run(
                "MATCH (e:Entity {tenant_id: $tenant_id}) RETURN e.name AS name LIMIT $limit",
                tenant_id=tenant_id, limit=limit,
            )
            return [rec["name"] for rec in result]
