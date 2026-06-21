"""Connector plugins.

Drop a new module in this package and decorate a BaseConnector subclass with
@register_connector("<key>"). Auto-discovery (app.registry.discover) imports
every module here on startup, so no core engine file needs editing.
"""
