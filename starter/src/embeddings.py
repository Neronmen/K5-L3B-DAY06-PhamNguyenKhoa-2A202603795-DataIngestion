from typing import Protocol

from sentence_transformers import SentenceTransformer

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_query(self, question: str) -> list[float]:
        ...


class MyEmbedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, batch_size: int = 32) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.query_prefix = BGE_QUERY_PREFIX if "bge" in model_name.lower() else ""
        self.model = SentenceTransformer(model_name, device="cpu")
        self.dimensions = self.model.get_embedding_dimension()
        self.max_seq_length = self.model.max_seq_length

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self.model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def embed_query(self, question: str) -> list[float]:
        vector = self.model.encode(self.query_prefix + question, normalize_embeddings=True)
        return vector.tolist()

    def describe(self) -> dict:
        return {
            "provider": "sentence-transformers (local, CPU)",
            "model": self.model_name,
            "dimensions": self.dimensions,
            "max_seq_length": self.max_seq_length,
            "query_prefix": self.query_prefix,
            "document_prefix": "",
            "normalized": True,
        }
