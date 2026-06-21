"""Knowledge-graph construction and indexing over Neo4j (ADR 0006).

EntityExtractor (spaCy NER) → RelationshipExtractor (LLM or rule-based) →
EntityResolver (dedupe) → GraphIndexer (Neo4j, property-tagged by tenant).
All Neo4j access goes through the shared, tenant-filtered Neo4jClient so
isolation can never be accidentally skipped.
"""
