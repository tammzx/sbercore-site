"""SQLAlchemy-модели (asyncpg). Схема — db/init.sql (единый источник истины DDL)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    """Пользователь. Сырой SberID — только здесь (auth-модуль), зашифрован."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sber_id_enc: Mapped[str] = mapped_column(Text, unique=True)  # Fernet-шифрование
    display_name: Mapped[str | None]
    role_tags: Mapped[dict] = mapped_column(JSONB, default=dict)  # {"client": true, ...}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Consent(Base):
    """Согласие субъекта ПДн (152-ФЗ ст. 9): цель, scope, срок, отзыв, доказательство."""

    __tablename__ = "consents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_ref: Mapped[str] = mapped_column(String(64), index=True)  # псевдоним
    consent_type: Mapped[str] = mapped_column(String(64))
    scope: Mapped[list] = mapped_column(JSONB, default=list)
    from_role: Mapped[str | None] = mapped_column(String(16))
    to_role: Mapped[str | None] = mapped_column(String(16))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    evidence_hash: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_consents_lookup", "user_ref", "consent_type", "from_role", "to_role"),
    )


class DialogueMessage(Base):
    """История диалога роли. content_enc — Fernet; hash — сверка целостности."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_ref: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))  # client | business | employee
    namespace: Mapped[str] = mapped_column(String(80), index=True)
    author: Mapped[str] = mapped_column(String(16))  # user | assistant
    content_enc: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEntry(Base):
    """Аудит: append-only, UPDATE/DELETE запрещены на уровне БД."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event: Mapped[str] = mapped_column(String(64))
    actor_ref: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True)
