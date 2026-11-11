"""RAG поверх pgvector с жёсткой namespace-изоляцией ролей.

Правило изоляции: поиск и запись выполняются ТОЛЬКО в namespace
``{user_ref}:{role}``. Namespace — обязательный аргумент каждого метода
(завершение «по умолчанию» невозможно), фильтр проверяется в SQL.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.ai.gigachat_client import GigaChatClient
from app.core.roles import Role


@dataclass(frozen=True)
class SearchHit:
    content: str
    namespace: str
    score: float


class RagIndex:
    """Векторный индекс диалогов и документов (PostgreSQL + pgvector)."""

    def __init__(self, session_factory, gigachat: GigaChatClient) -> None:
        self._session_factory = session_factory
        self._gigachat = gigachat

    async def upsert(
        self,
        *,
        namespace: str,
        role: Role,
        user_ref: str,
        texts: list[str],
    ) -> int:
        """Индексировать тексты в namespace роли. Возвращает число записей."""
        import sqlalchemy as sa

        if not texts:
            return 0
        vectors = await self._gigachat.get_embeddings(texts)
        async with self._session_factory() as session:
            for text, vec in zip(texts, vectors):
                await session.execute(
                    sa.text(
                        "INSERT INTO embeddings (user_ref, role, namespace, content, embedding) "
                        "VALUES (:user_ref, :role, :namespace, :content, :embedding)"
                    ),
                    {
                        "user_ref": user_ref,
                        "role": role.value,
                        "namespace": namespace,
                        "content": text,
                        "embedding": str(vec),
                    },
                )
            await session.commit()
        return len(texts)

    async def search(self, namespace: str, query: str, *, k: int = 5) -> list[SearchHit]:
        """Поиск ближайших фрагментов строго внутри namespace роли."""
        import sqlalchemy as sa

        query_vec = (await self._gigachat.get_embeddings([query]))[0]
        # Фильтр по namespace — на уровне SQL: поиск вне роли невозможен.
        # Оператор `<=>` — косинусная дистанция pgvector; vec передаём строкой.
        sql = sa.text(
            "SELECT content, namespace, embedding <=> :vec AS distance "
            "FROM embeddings WHERE namespace = :namespace "
            "ORDER BY embedding <=> :vec LIMIT :k"
        )
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    sql,
                    {"vec": str(query_vec), "namespace": namespace, "k": k},
                )
            ).all()
        return [
            SearchHit(content=r[0], namespace=r[1], score=1.0 - float(r[2]))
            for r in rows
        ]
