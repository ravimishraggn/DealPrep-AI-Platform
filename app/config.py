"""Application settings for the full ingestion + retrieval platform.

Every knob is env-overridable (prefix ``DEALPREP_``) so the same code runs
locally against the docker-compose stack and in a deployed environment with no
edits. The three platform data stores (Postgres, ChromaDB, Neo4j) and the
embedding/LLM models are all configured here.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = parent of the app/ package.
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Typed application configuration, loaded from env / ``.env``.

    Attributes mirror the platform data-layer strategy: one relational store
    (Postgres), one vector store (ChromaDB), one graph store (Neo4j), plus the
    local embedding model and the optional Claude relationship-extraction model.
    """

    model_config = SettingsConfigDict(env_prefix="DEALPREP_", env_file=".env", extra="ignore")

    # --- Postgres: operational metadata + structured records (JSONB) + FTS (tsvector) ---
    # ADR 0003. Default points at the docker-compose Postgres service.
    database_url: str = "postgresql+psycopg://dealprep:dealprep@localhost:5432/dealprep"

    # --- ChromaDB: vector store, one collection per tenant (ADR 0005) ---
    # Embedded persistent client writes to this directory; no separate server.
    chroma_dir: Path = BASE_DIR / "data" / "chroma"
    embedding_model: str = "all-MiniLM-L6-v2"  # sentence-transformers model for the 'minilm' backend

    # --- Pipeline strategy defaults (ADR 0009/0010/0011/0012) ---
    # Platform-wide defaults; a tenant may override via its pipeline profile.
    default_chunking: str = "section_aware"
    default_embedding: str = "minilm"
    default_vector_store: str = "chroma"

    # --- Neo4j: entity/relationship graph, property-based tenant tagging (ADR 0006) ---
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "dealprep-graph"

    # --- Relationship extraction LLM (optional) ---
    # When an Anthropic API key is present the RelationshipExtractor uses Claude;
    # otherwise it falls back to a deterministic rule-based extractor so the
    # pipeline still runs end-to-end (ADR 0006 §relationship extraction).
    anthropic_api_key: str | None = None
    relationship_model: str = "claude-haiku-4-5-20251001"  # fast/cheap for triple extraction

    # --- Tenant-namespaced raw landing output (ADR 0001 D7) ---
    data_dir: Path = BASE_DIR / "data"

    # --- Scheduler dev demo mode (ADR 0001 D6) ---
    dev_mode: bool = True
    dev_min_interval_seconds: int = 10

    # --- Chunking heuristics (Phase 5-6 DocumentProcessor) ---
    chunk_target_chars: int = 1200   # soft target size of a text chunk
    chunk_overlap_chars: int = 150   # carry-over between adjacent chunks for context

    @property
    def data_dir_path(self) -> Path:
        """Filesystem root for tenant-namespaced raw landing output."""
        return Path(self.data_dir)

    @property
    def chroma_dir_path(self) -> Path:
        """Filesystem root for the embedded ChromaDB persistent store."""
        return Path(self.chroma_dir)


settings = Settings()
