"""Extractor plugins, registered by ``format_type``.

Add a new format by dropping a module here that subclasses ``BaseExtractor`` and
is decorated with ``@register_extractor("<format>")``. Auto-discovery imports
every module in this package at startup, so no core engine file changes.
"""
