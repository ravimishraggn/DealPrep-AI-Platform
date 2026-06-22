"""Pluggable embedding backends (ADR 0010).

An embedder turns text into vectors. Backends are registered by name and selected
per tenant via the pipeline profile. Add one by dropping a module that subclasses
``BaseEmbedder`` and is decorated with ``@register_embedder("<name>")``.
"""
