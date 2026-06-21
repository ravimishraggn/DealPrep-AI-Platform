"""Format-aware extraction → processing → indexing → retrieval pipeline (Phase 5-6).

Stages, in order:
    Connector.fetch() -> RawRecord(format_type)   [connectors]
      -> FormatRouter -> BaseExtractor             [extractors]
      -> ExtractionResult(text_documents, structured_records)
      -> DocumentProcessor (chunk text / pass structured)
      -> [VectorIndexer | StructuredIndexer | GraphIndexer]  (parallel fan-out)
      -> Unified Search API
"""
