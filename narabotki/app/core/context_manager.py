"""RoleContextManager v2.1 — единый контекст пользователя + ролевые анклавы.

Ключевая модель (по ТЗ v2.1 — «агент-оператор»):

1. **Один контекст на пользователя.** История диалога едина для всех трёх
   ролей: namespace ``{user_ref}:unified``. При переключении роли
   (client → business → employee) разговор продолжается бесшовно —
   «переносить» общую историю не нужно.
2. **Общий слой обесличен.** Shared-сообщения хранятся после маскирования
   реквизитов (152-ФЗ ст. 5 ч. 5 — минимизация), поэтому безопасно видны
   во всех ролях и не нарушают банковскую тайну (395-1 ст. 26).
3. **Ролевые анклавы.** Чувствительные данные (счета, платежи, HR-записи)
   живут в namespace ``{user_ref}:{role}:enclave`` и видны ТОЛЬКО в своей
   роли. В чужую роль и в общий слой не «протекают» никогда; вывод
   отдельной записи в общий слой — только обесличенной и по согласию
   ``context_transfer``.
4. **Псевдонимизация.** Везде user_ref = HMAC-SHA256(user_id), SberID не
   покидает auth-модуль.
5. **Согласия до обработки ПДн.** ``get_context()`` проверяет
   ``pdn_dialogue:{role}`` (152-ФЗ ст. 9). Для сотрудника основание —
   трудовой договор (ТК РФ ст. 86), согласие не требуется.
6. **Аудит.** Все операции — в append-only журнал с хеш-цепочкой.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from app.core.audit import AuditLog
from app.core.exceptions import (
    ConsentRequiredError,
    RoleNotAvailableError,
)
from app.core.isolation import role_namespace, sanitize_for_transfer, unified_namespace
from app.core.roles import ConsentType, Role, ROLE_SPECS


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Чувствительность сообщений
# --------------------------------------------------------------------------

class Sensitivity(str, Enum):
    SHARED = "shared"    # обесличенное, видно во всех ролях
    ENCLAVE = "enclave"  # чувствительное, видно только в своей роли


# --------------------------------------------------------------------------
# Контекст (вид) пользователя
# --------------------------------------------------------------------------

@dataclass
class ContextView:
    """Единый контекст пользователя, отфильтрованный по активной роли.

    messages — общая история (shared) + анклав активной роли.
    Сообщения чужих анклавов в этом списке отсутствуют по построению.
    """

    role: Role
    unified_namespace: str
    enclave_namespace: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    enclave_facts: dict[str, Any] = field(default_factory=dict)
    audit_reason: str = ""

    def last(self, n: int) -> list[dict[str, Any]]:
        return self.messages[-n:]

    @property
    def is_continuous(self) -> bool:
        """Признак единого контекста: история содержит сообщения нескольких ролей."""
        return len({m.get("role_at_creation") for m in self.messages}) > 1


# --------------------------------------------------------------------------
# Порты
# --------------------------------------------------------------------------

class ContextStore(Protocol):
    """Хранилище контекстов (прод: Redis TTL + PostgreSQL long-term)."""

    async def load(self, namespace: str) -> dict[str, Any]: ...

    async def save(self, namespace: str, state: dict[str, Any], ttl_seconds: int | None = None) -> None: ...


class ConsentPort(Protocol):

    async def check(
        self,
        user_ref: str,
        consent_type: str,
        *,
        from_role: str | None = None,
        to_role: str | None = None,
        scope: str | None = None,
    ) -> bool: ...

    async def active_consent_id(
        self,
        user_ref: str,
        consent_type: str,
        *,
        from_role: str | None = None,
        to_role: str | None = None,
    ) -> str | None: ...


# --------------------------------------------------------------------------
# Переключение роли и вывод анклава
# --------------------------------------------------------------------------

@dataclass
class EnclavePromotionRequest:
    """Вывод выбранных записей анклава в общий слой (обезличенно, по согласию)."""

    message_ids: list[int]   # индексы сообщений АНКЛАВА активной роли
    note: str | None = None


@dataclass
class SwitchResult:
    from_role: Role | None
    to_role: Role
    context_preserved: bool          # единый контекст сохранён (всегда True)
    dialogue_consent_granted: bool   # согласие на диалог в новой роли


@dataclass
class PromotionResult:
    promoted: int
    consent_id: str | None


# --------------------------------------------------------------------------

class RoleContextManager:
    """Единый контекст трёх ролей одного физического лица + ролевые анклавы.

    Инстанс привязан к пользователю (псевдониму) и сессии; создаётся на запрос.
    """

    SESSION_NAMESPACE_SUFFIX = "_session"

    def __init__(
        self,
        *,
        user_ref: str,
        available_roles: frozenset[Role] | set[Role],
        active_role: Role | None,
        store: ContextStore,
        consents: ConsentPort,
        audit: AuditLog,
        session_ttl_seconds: int = 12 * 60 * 60,
    ) -> None:
        self._user_ref = user_ref
        self._available_roles = available_roles
        self._active_role = active_role
        self._store = store
        self._consents = consents
        self._audit_log = audit
        self._session_ttl = session_ttl_seconds

    @classmethod
    async def create(
        cls,
        *,
        user_ref: str,
        available_roles: frozenset[Role] | set[Role],
        store: ContextStore,
        consents: ConsentPort,
        audit: AuditLog,
        default_role: Role = Role.CLIENT,
        session_ttl_seconds: int = 12 * 60 * 60,
    ) -> "RoleContextManager":
        """Асинхронная фабрика: восстанавливает активную роль из сессии."""
        state = await store.load(f"{user_ref}:{cls.SESSION_NAMESPACE_SUFFIX}")
        active_raw = state.get("active_role")
        active = Role(active_raw) if active_raw in [r.value for r in Role] else None
        if active is not None and active not in available_roles:
            active = None
        if active is None:
            active = default_role if default_role in available_roles else None
        return cls(
            user_ref=user_ref,
            available_roles=available_roles,
            active_role=active,
            store=store,
            consents=consents,
            audit=audit,
            session_ttl_seconds=session_ttl_seconds,
        )

    # -- публичный API -------------------------------------------------------

    @property
    def active_role(self) -> Role | None:
        return self._active_role

    @property
    def available_roles(self) -> set[Role]:
        return set(self._available_roles)

    @property
    def unified_namespace(self) -> str:
        return unified_namespace(self._user_ref)

    @property
    def user_ref(self) -> str:
        return self._user_ref

    async def switch_role(self, target: Role) -> SwitchResult:
        """Переключить активную роль.

        Единый контекст НЕ переносится — он один на пользователя и остаётся
        непрерывным (context_preserved=True). Меняется только «оптика»:
        анклавы чужих ролей скрываются, системный промпт и инструменты
        агент-оператора меняются на целевую роль.
        """
        if target not in self._available_roles:
            raise RoleNotAvailableError(target.value)

        source = self._active_role
        if target != source:
            self._active_role = target
            await self._persist_active_role()

        await self._audit(
            "role.switched",
            {
                "from": source.value if source else None,
                "to": target.value,
                "context": "unified_preserved",
            },
        )
        return SwitchResult(
            from_role=source,
            to_role=target,
            context_preserved=True,
            dialogue_consent_granted=await self.check_consent(ConsentType.PDN_DIALOGUE, role=target),
        )

    async def get_context(self, role: Role | None = None) -> ContextView:
        """Единый контекст пользователя в «оптике» активной (или указанной) роли.

        - Общая история (shared, обесличенная) видна всегда — контекст один.
        - Анклав активной роли (чувствительные записи) виден в своей роли:
          client/business — при согласии pdn_dialogue, employee — по ТК РФ.
        - Анклавы ЧУЖИХ ролей не возвращаются никогда (банковская тайна, ТК РФ).
        """
        role = role or self._active_role
        if role is None:
            raise RoleNotAvailableError("роль не выбрана")
        if role not in self._available_roles:
            raise RoleNotAvailableError(role.value)

        spec = ROLE_SPECS[role]
        audit_reason = f"legal_base={spec.legal_base.value}"
        if spec.consent_required_for_dialogue:
            if not await self.check_consent(ConsentType.PDN_DIALOGUE, role=role):
                raise ConsentRequiredError(ConsentType.PDN_DIALOGUE, scope=role.value)
            audit_reason += ",consent=granted"
        else:
            audit_reason += ",consent=not_required_tk86"

        unified_state = await self._store.load(self.unified_namespace)
        enclave_ns = role_namespace(self._user_ref, role) + ":enclave"
        enclave_state = await self._store.load(enclave_ns)

        visible = [
            m for m in unified_state.get("messages", [])
            if m.get("sensitivity") == Sensitivity.SHARED.value
            or m.get("role_at_creation") == role.value
        ]

        view = ContextView(
            role=role,
            unified_namespace=self.unified_namespace,
            enclave_namespace=enclave_ns,
            messages=visible,
            enclave_facts=enclave_state.get("facts", {}),
            audit_reason=audit_reason,
        )
        await self._audit(
            "context.loaded",
            {
                "role": role.value,
                "unified": self.unified_namespace,
                "visible_messages": len(visible),
                "enclave_facts": len(view.enclave_facts),
                "reason": audit_reason,
            },
        )
        return view

    async def add_message(
        self,
        author: str,
        content: str,
        *,
        sensitive: bool = False,
        role: Role | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Добавить сообщение в ЕДИНЫЙ контекст пользователя.

        - shared (по умолчанию): содержимое маскируется и видно во всех ролях.
        - sensitive=True: сообщение уходит в АНКЛАВ активной роли без маскирования
          (банковская тайна / HR-данные); в общий слой попадает только факт
          существования сообщения (без содержимого) — сохраняется непрерывность
          диалога, но не секретные данные.
        """
        role = role or self._active_role
        if role is None:
            raise RoleNotAvailableError("роль не выбрана")

        message = {
            "author": author,
            "content": sanitize_for_transfer(content) if not sensitive else "••••••",
            "sensitivity": Sensitivity.ENCLAVE.value if sensitive else Sensitivity.SHARED.value,
            "role_at_creation": role.value,
            "hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "ts": utcnow(),
            "metadata": metadata or {},
        }

        unified_state = await self._store.load(self.unified_namespace)
        unified_state.setdefault("messages", []).append(message)
        await self._store.save(self.unified_namespace, unified_state, ttl_seconds=self._session_ttl)

        if sensitive:
            enclave_ns = role_namespace(self._user_ref, role) + ":enclave"
            enclave_state = await self._store.load(enclave_ns)
            enclave_state.setdefault("messages", []).append(
                {**message, "content": content}  # сырые данные — только в анклаве
            )
            await self._store.save(enclave_ns, enclave_state, ttl_seconds=self._session_ttl)

        await self._audit(
            "message.appended",
            {"role": role.value, "sensitivity": message["sensitivity"], "hash": message["hash"]},
        )
        return message

    async def promote_to_shared(self, message_ids: list[int]) -> PromotionResult:
        """Вывести выбранные записи анклава активной роли в общий слой.

        Только обесличенно (маскирование) и только при активном согласии
        ``context_transfer:{role}->shared`` (152-ФЗ ст. 9 — отдельная цель).
        """
        role = self._active_role
        if role is None:
            raise RoleNotAvailableError("роль не выбрана")

        allowed = await self._consents.check(
            self._user_ref,
            ConsentType.CONTEXT_TRANSFER.value,
            from_role=role.value,
            to_role="shared",
        )
        if not allowed:
            raise ConsentRequiredError(
                ConsentType.CONTEXT_TRANSFER,
                scope=f"{role.value}->shared",
            )

        consent_id = await self._consents.active_consent_id(
            self._user_ref,
            ConsentType.CONTEXT_TRANSFER.value,
            from_role=role.value,
            to_role="shared",
        )

        enclave_ns = role_namespace(self._user_ref, role) + ":enclave"
        enclave_state = await self._store.load(enclave_ns)
        enclave_messages = enclave_state.get("messages", [])
        selected = [enclave_messages[i] for i in message_ids if 0 <= i < len(enclave_messages)]

        unified_state = await self._store.load(self.unified_namespace)
        promoted = 0
        for m in selected:
            unified_state.setdefault("messages", []).append({
                **m,
                "content": sanitize_for_transfer(m["content"]),  # обесличивание
                "sensitivity": Sensitivity.SHARED.value,
                "metadata": {**m.get("metadata", {}), "promoted_from": role.value, "consent_id": consent_id},
            })
            promoted += 1
        await self._store.save(self.unified_namespace, unified_state, ttl_seconds=self._session_ttl)

        await self._audit(
            "context.promoted",
            {"role": role.value, "consent_id": consent_id, "count": promoted},
        )
        return PromotionResult(promoted=promoted, consent_id=consent_id)

    async def check_consent(
        self,
        consent_type: ConsentType,
        *,
        role: Role | None = None,
        from_role: str | None = None,
        to_role: str | None = None,
        scope: str | None = None,
    ) -> bool:
        """Проверить согласие субъекта ПДн.

        - ``pdn_dialogue`` — scope по роли; для employee всегда True
          (ТК РФ ст. 86: основание — трудовой договор, согласие не используется).
        - ``context_transfer`` — по паре from_role→to_role (например, client→shared).
        """
        if consent_type is ConsentType.PDN_DIALOGUE:
            role = role or self._active_role
            if role is None:
                raise RoleNotAvailableError("роль не выбрана")
            if not ROLE_SPECS[role].consent_required_for_dialogue:
                return True  # ТК РФ ст. 86
            scope = scope or role.value

        return await self._consents.check(
            self._user_ref,
            consent_type.value,
            from_role=from_role,
            to_role=to_role,
            scope=scope,
        )

    async def isolate_data(self, key: str, value: Any, *, role: Role | None = None) -> None:
        """Сохранить чувствительные данные в анклаве роли (банковская тайна / ТК РФ)."""
        role = role or self._active_role
        if role is None:
            raise RoleNotAvailableError("роль не выбрана")
        ns = role_namespace(self._user_ref, role) + ":enclave"
        state = await self._store.load(ns)
        state.setdefault("facts", {})[key] = {"value": value, "stored_at": utcnow()}
        await self._store.save(ns, state, ttl_seconds=self._session_ttl)
        await self._audit("enclave.written", {"role": role.value, "key": key})

    async def get_isolated_data(self, key: str, *, role: Role | None = None) -> Any | None:
        """Читать данные АНКЛАВА активной роли (чужой анклав недоступен)."""
        role = role or self._active_role
        if role is None:
            raise RoleNotAvailableError("роль не выбрана")
        ns = role_namespace(self._user_ref, role) + ":enclave"
        state = await self._store.load(ns)
        return state.get("facts", {}).get(key, {}).get("value")

    # -- внутреннее ------------------------------------------------------------

    async def _persist_active_role(self) -> None:
        ns = f"{self._user_ref}:{self.SESSION_NAMESPACE_SUFFIX}"
        await self._store.save(
            ns,
            {"active_role": self._active_role.value, "updated_at": utcnow()},
            ttl_seconds=self._session_ttl,
        )

    async def _audit(self, event: str, payload: dict[str, Any]) -> None:
        await self._audit_log.append(event, self._user_ref, payload)
