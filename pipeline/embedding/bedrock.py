"""AWS Bedrock (Titan) embeddings backend (POC STUB)."""
from __future__ import annotations

from pipeline.embedding.base import BaseEmbedder, register_embedder


@register_embedder("bedrock")
class BedrockEmbedder(BaseEmbedder):
    """STUB: Amazon Titan embeddings via AWS Bedrock.

    Registered as a selectable option but not implemented for V1. Real use needs
    AWS credentials/IAM, region config, and Bedrock access — appropriate for
    AWS-resident, data-residency-sensitive tenants (see ADR 0010). Raises on use.
    """

    dim = 1024
    implemented = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Not implemented — see class docstring and ADR 0010."""
        raise NotImplementedError("bedrock embedder is a POC stub (see ADR 0010)")
