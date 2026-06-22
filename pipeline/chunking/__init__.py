"""Pluggable chunking strategies (ADR 0009).

A chunking strategy turns document text into retrieval units. Strategies are
registered by name and selected per tenant via the pipeline profile. Add a
strategy by dropping a module here that subclasses ``BaseChunker`` and is
decorated with ``@register_chunker("<name>")`` — no core change.
"""
