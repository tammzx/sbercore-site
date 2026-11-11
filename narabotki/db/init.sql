-- Схема SberOne Assistant. Локализация ПДн — РФ (152-ФЗ ст. 18 ч. 5).
CREATE EXTENSION IF NOT EXISTS vector;

-- Пользователи: сырой SberID — только зашифрованный (auth-модуль)
CREATE TABLE IF NOT EXISTS users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sber_id_enc   text NOT NULL UNIQUE,
    display_name  text,
    role_tags     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- Согласия (152-ФЗ ст. 9): цель, scope, срок, отзыв, доказательство
CREATE TABLE IF NOT EXISTS consents (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_ref      text NOT NULL,
    consent_type  text NOT NULL,
    scope         jsonb NOT NULL DEFAULT '[]'::jsonb,
    from_role     text,
    to_role       text,
    granted_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz,
    revoked_at    timestamptz,
    evidence_hash text NOT NULL,
    CHECK (consent_type IN ('pdn_dialogue', 'context_transfer',
                            'banking_data_analysis', 'ml_improvement')),
    CHECK (from_role IS NULL OR from_role IN ('client', 'business', 'employee')),
    CHECK (to_role   IS NULL OR to_role   IN ('client', 'business', 'employee'))
);
CREATE INDEX IF NOT EXISTS ix_consents_lookup
    ON consents (user_ref, consent_type, from_role, to_role);

-- История диалогов: namespace = {user_ref}:{role} — изоляция на уровне данных
CREATE TABLE IF NOT EXISTS messages (
    id            bigserial PRIMARY KEY,
    user_ref      text NOT NULL,
    role          text NOT NULL CHECK (role IN ('client', 'business', 'employee')),
    namespace     text NOT NULL,
    author        text NOT NULL CHECK (author IN ('user', 'assistant')),
    content_enc   text NOT NULL,
    content_hash  text NOT NULL,
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_messages_namespace ON messages (namespace, created_at);

-- Векторный индекс контекста (pgvector, cosine)
CREATE TABLE IF NOT EXISTS embeddings (
    id         bigserial PRIMARY KEY,
    user_ref   text NOT NULL,
    role       text NOT NULL CHECK (role IN ('client', 'business', 'employee')),
    namespace  text NOT NULL,
    content    text NOT NULL,
    embedding  vector(1024) NOT NULL,   -- EmbeddingsGigaR
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_embeddings_hnsw
    ON embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS ix_embeddings_namespace ON embeddings (namespace);

-- Аудит: append-only, хеш-цепочка, хранение 5 лет (152-ФЗ ст. 18.1 ч. 2)
CREATE TABLE IF NOT EXISTS audit_log (
    id         bigserial PRIMARY KEY,
    ts         timestamptz NOT NULL DEFAULT now(),
    event      text NOT NULL,
    actor_ref  text NOT NULL,
    payload    jsonb NOT NULL,
    prev_hash  text NOT NULL,
    entry_hash text NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_audit_actor ON audit_log (actor_ref, ts);

-- Неизменяемость аудита на уровне БД: правила глушат UPDATE/DELETE
CREATE OR REPLACE RULE audit_no_update AS ON UPDATE TO audit_log DO INSTEAD NOTHING;
CREATE OR REPLACE RULE audit_no_delete AS ON DELETE TO audit_log DO INSTEAD NOTHING;
