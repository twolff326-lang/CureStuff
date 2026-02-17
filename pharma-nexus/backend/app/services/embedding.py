"""Vector embedding service using sentence-transformers.

Generates 384-dimensional embeddings for biological text using
all-MiniLM-L6-v2 for semantic similarity search via pgvector.

Model: sentence-transformers/all-MiniLM-L6-v2
  - 384-dimensional vectors
  - ~100 embeddings/sec in batches on CPU
  - ~90MB model size
  - Good quality for scientific text
"""

import logging
from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drug import Drug
from app.models.literature import Literature
from app.models.target import Target

logger = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


class EmbeddingService:
    """Generates and manages vector embeddings for semantic search."""

    _model: SentenceTransformer | None = None

    @classmethod
    def get_model(cls) -> SentenceTransformer:
        """Load model once (singleton pattern)."""
        if cls._model is None:
            logger.info("Loading embedding model: %s", MODEL_NAME)
            cls._model = SentenceTransformer(MODEL_NAME)
            logger.info("Embedding model loaded (%d dimensions)", EMBEDDING_DIM)
        return cls._model

    def embed_texts(
        self, texts: list[str], batch_size: int = 64
    ) -> list[list[float]]:
        """Embed a list of texts into 384-dim vectors.

        Args:
            texts: list of strings to embed
            batch_size: process in batches for memory efficiency

        Returns:
            list of 384-dimensional float vectors
        """
        model = self.get_model()
        embeddings = model.encode(
            texts, batch_size=batch_size, show_progress_bar=len(texts) > 100
        )
        return embeddings.tolist()

    def embed_single(self, text: str) -> list[float]:
        """Embed a single text. Use embed_texts for batches."""
        return self.embed_texts([text])[0]

    # ------------------------------------------------------------------
    # Bulk embedding generation
    # ------------------------------------------------------------------

    async def embed_literature(
        self, session: AsyncSession, batch_size: int = 500
    ) -> int:
        """Generate embeddings for all literature records missing them.

        Embeds title + abstract concatenated.
        Returns: number of records embedded.
        """
        result = await session.execute(
            select(Literature.id, Literature.title, Literature.abstract).where(
                Literature.abstract_embedding.is_(None),
                Literature.abstract.isnot(None),
                Literature.abstract != "",
            )
        )
        rows = result.all()
        logger.info("Embedding %d literature records", len(rows))

        if not rows:
            return 0

        total = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            texts = [
                f"{row[1] or ''} {row[2] or ''}" for row in batch
            ]
            embeddings = self.embed_texts(texts)

            for row, embedding in zip(batch, embeddings):
                await session.execute(
                    update(Literature)
                    .where(Literature.id == row[0])
                    .values(abstract_embedding=embedding)
                )

            total += len(batch)
            if total % 5000 == 0:
                await session.commit()
                logger.info("Embedded %d/%d literature records", total, len(rows))

        await session.commit()
        logger.info("Literature embedding complete: %d records", total)
        return total

    async def embed_targets(
        self, session: AsyncSession, batch_size: int = 500
    ) -> int:
        """Generate embeddings for target function descriptions.

        Embeds: "{gene_symbol}: {function_description}"
        """
        result = await session.execute(
            select(
                Target.id, Target.gene_symbol, Target.function_description
            ).where(
                Target.embedding.is_(None),
                Target.function_description.isnot(None),
                Target.function_description != "",
            )
        )
        rows = result.all()
        logger.info("Embedding %d target descriptions", len(rows))

        if not rows:
            return 0

        texts = [
            f"{row[1] or ''}: {row[2] or ''}" for row in rows
        ]
        embeddings = self.embed_texts(texts, batch_size=batch_size)

        for row, embedding in zip(rows, embeddings):
            await session.execute(
                update(Target)
                .where(Target.id == row[0])
                .values(embedding=embedding)
            )

        await session.commit()
        logger.info("Target embedding complete: %d records", len(rows))
        return len(rows)

    async def embed_drugs(
        self, session: AsyncSession, batch_size: int = 500
    ) -> int:
        """Generate embeddings for drug mechanism descriptions.

        Embeds: "{drug_name}: {mechanism_of_action}"
        """
        result = await session.execute(
            select(
                Drug.id, Drug.name, Drug.mechanism_of_action
            ).where(
                Drug.mechanism_embedding.is_(None),
                Drug.mechanism_of_action.isnot(None),
                Drug.mechanism_of_action != "",
            )
        )
        rows = result.all()
        logger.info("Embedding %d drug mechanisms", len(rows))

        if not rows:
            return 0

        texts = [
            f"{row[1] or ''}: {row[2] or ''}" for row in rows
        ]
        embeddings = self.embed_texts(texts, batch_size=batch_size)

        for row, embedding in zip(rows, embeddings):
            await session.execute(
                update(Drug)
                .where(Drug.id == row[0])
                .values(mechanism_embedding=embedding)
            )

        await session.commit()
        logger.info("Drug embedding complete: %d records", len(rows))
        return len(rows)

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    async def create_vector_indexes(self, session: AsyncSession) -> None:
        """Create IVFFlat indexes for vector similarity search.

        Should be called AFTER bulk embedding insertion for optimal index.
        """
        indexes = [
            (
                "ix_literature_abstract_embedding",
                "literature",
                "abstract_embedding",
                100,
            ),
            (
                "ix_targets_embedding",
                "targets",
                "embedding",
                50,
            ),
            (
                "ix_drugs_mechanism_embedding",
                "drugs",
                "mechanism_embedding",
                50,
            ),
        ]

        for idx_name, table, column, lists in indexes:
            try:
                await session.execute(
                    text(f"DROP INDEX IF EXISTS {idx_name}")
                )
                await session.execute(
                    text(
                        f"CREATE INDEX {idx_name} ON {table} "
                        f"USING ivfflat ({column} vector_cosine_ops) "
                        f"WITH (lists = {lists})"
                    )
                )
                await session.commit()
                logger.info("Created IVFFlat index %s (lists=%d)", idx_name, lists)
            except Exception as exc:
                logger.warning("Failed to create index %s: %s", idx_name, exc)
                await session.rollback()
