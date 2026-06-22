"""Pluggable vector store backends (ADR 0011).

A vector store persists and similarity-searches embeddings, isolated per tenant.
Backends are registered by name and selected per tenant via the pipeline profile.
Add one by dropping a module that subclasses ``BaseVectorStore`` and is decorated
with ``@register_vector_store("<name>")``.
"""
