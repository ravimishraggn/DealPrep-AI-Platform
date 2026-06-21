"""The three indexers and their search counterparts.

Vector (ChromaDB), Structured (Postgres), and Graph (Neo4j) each consume the
document processor's output and expose a tenant-scoped search method. They run as
an independent parallel fan-out after document processing (ADR 0007).
"""
