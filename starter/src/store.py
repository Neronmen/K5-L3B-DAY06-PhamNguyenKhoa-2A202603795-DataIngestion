import json
from typing import Any

import psycopg

from .models import Chunk

FILTER_COLUMNS = {"food_category", "document_id", "section"}


def _to_vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.7f}" for value in vector) + "]"


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int, table: str = "chunks") -> None:
        if not database_url:
            raise ValueError("DATABASE_URL is empty")
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        if not table.isidentifier():
            raise ValueError("table name must be a plain identifier")
        self.database_url = database_url
        self.dimensions = dimensions
        self.table = table

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, connect_timeout=5)

    def create_schema(self) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    strategy      text    NOT NULL,
                    chunk_id      text    NOT NULL,
                    document_id   text    NOT NULL,
                    source        text    NOT NULL,
                    page          integer NOT NULL CHECK (page > 0),
                    page_label    text,
                    food_category text,
                    section       text,
                    text          text    NOT NULL,
                    metadata      jsonb   NOT NULL DEFAULT '{{}}',
                    embedding     vector({self.dimensions}) NOT NULL,
                    updated_at    timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (strategy, chunk_id)
                )
                """
            )
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {self.table}_filter_idx ON {self.table} (strategy, food_category)"
            )
            cursor.execute(
                "SELECT atttypmod FROM pg_attribute WHERE attrelid = %s::regclass AND attname = 'embedding'",
                (self.table,),
            )
            stored_dimensions = cursor.fetchone()[0]
            if stored_dimensions != self.dimensions:
                raise ValueError(
                    f"Table {self.table} stores vector({stored_dimensions}) but the model has {self.dimensions} dimensions"
                )

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> dict[str, int]:
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
        if any(len(vector) != self.dimensions for vector in vectors):
            raise ValueError(f"every vector must have {self.dimensions} dimensions")
        ids = [chunk.chunk_id for chunk in chunks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate chunk_id in one upsert batch")

        rows = [
            (
                strategy,
                chunk.chunk_id,
                chunk.document_id,
                chunk.source,
                chunk.page,
                chunk.metadata.get("page_label"),
                chunk.metadata.get("food_category"),
                chunk.metadata.get("section"),
                chunk.text,
                json.dumps(chunk.metadata, ensure_ascii=False),
                _to_vector_literal(vector),
            )
            for chunk, vector in zip(chunks, vectors)
        ]
        document_ids = sorted({chunk.document_id for chunk in chunks})

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(
                f"""
                INSERT INTO {self.table}
                    (strategy, chunk_id, document_id, source, page, page_label,
                     food_category, section, text, metadata, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector)
                ON CONFLICT (strategy, chunk_id) DO UPDATE SET
                    document_id = EXCLUDED.document_id,
                    source = EXCLUDED.source,
                    page = EXCLUDED.page,
                    page_label = EXCLUDED.page_label,
                    food_category = EXCLUDED.food_category,
                    section = EXCLUDED.section,
                    text = EXCLUDED.text,
                    metadata = EXCLUDED.metadata,
                    embedding = EXCLUDED.embedding,
                    updated_at = now()
                """,
                rows,
            )
            cursor.execute(
                f"""
                DELETE FROM {self.table}
                WHERE strategy = %s AND document_id = ANY(%s) AND NOT (chunk_id = ANY(%s))
                """,
                (strategy, document_ids, ids),
            )
            removed = cursor.rowcount
        return {"upserted": len(rows), "removed_obsolete": removed}

    def search(self, strategy: str, query_vector: list[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if len(query_vector) != self.dimensions:
            raise ValueError(f"query vector must have {self.dimensions} dimensions")
        conditions, params = ["strategy = %s"], [strategy]
        for key, value in (filters or {}).items():
            if key not in FILTER_COLUMNS:
                raise ValueError(f"filter on {key!r} is not allowed; use one of {sorted(FILTER_COLUMNS)}")
            conditions.append(f"{key} = %s")
            params.append(value)
        literal = _to_vector_literal(query_vector)

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT chunk_id, document_id, source, page, page_label, food_category, section,
                       text, metadata, 1 - (embedding <=> %s::vector) AS score
                FROM {self.table}
                WHERE {' AND '.join(conditions)}
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                [literal, *params, literal, top_k],
            )
            columns = [description.name for description in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def count(self, strategy: str | None = None) -> int:
        with self._connect() as connection, connection.cursor() as cursor:
            if strategy is None:
                cursor.execute(f"SELECT count(*) FROM {self.table}")
            else:
                cursor.execute(f"SELECT count(*) FROM {self.table} WHERE strategy = %s", (strategy,))
            return cursor.fetchone()[0]

    def delete_strategy(self, strategy: str) -> int:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {self.table} WHERE strategy = %s", (strategy,))
            return cursor.rowcount
