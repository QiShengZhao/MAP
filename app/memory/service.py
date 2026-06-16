import re
from datetime import datetime
from typing import Iterable

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import MemoryItem, SessionSummary

VALID_SCOPES = {"user", "workspace", "session", "run"}
VALID_KINDS = {"fact", "preference", "decision", "task", "summary", "note"}
SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*\S{8,}|sk-[A-Za-z0-9]{16,}"
)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[\w\u4e00-\u9fff]+", text or "") if len(t) > 1}


def _score(item: MemoryItem, query: str) -> float:
    q = _tokens(query)
    text = f"{item.title} {item.content}"
    words = _tokens(text)
    lexical = len(q & words) / max(len(q), 1)
    return lexical + (0.25 if item.pinned else 0) + float(item.confidence or 0) * 0.1


def _visible_predicates(*, workspace_id: str | None, session_id: str | None,
                        run_id: str | None, user_id: str | None):
    return or_(
        MemoryItem.scope == "workspace",
        (MemoryItem.scope == "user") & (MemoryItem.user_id == user_id),
        (MemoryItem.scope == "session") & (MemoryItem.session_id == session_id),
        (MemoryItem.scope == "run") & (MemoryItem.run_id == run_id),
    ), or_(MemoryItem.workspace_id == None, MemoryItem.workspace_id == workspace_id)


class MemoryService:
    @staticmethod
    async def write(
        db: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        scope: str = "workspace",
        kind: str = "note",
        title: str = "",
        content: str = "",
        source_type: str = "system",
        source_id: str = "",
        confidence: float = 0.6,
        pinned: bool = False,
        embedding: list | None = None,
        expires_at: datetime | None = None,
    ) -> MemoryItem:
        if scope not in VALID_SCOPES:
            raise ValueError(f"invalid memory scope: {scope}")
        if kind not in VALID_KINDS:
            raise ValueError(f"invalid memory kind: {kind}")
        item = MemoryItem(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            scope=scope,
            kind=kind,
            title=title[:255],
            content=content,
            source_type=source_type,
            source_id=source_id,
            confidence=max(0.0, min(float(confidence), 1.0)),
            pinned=pinned,
            embedding=embedding,
            expires_at=expires_at,
        )
        db.add(item)
        await db.flush()
        return item

    @staticmethod
    async def search(
        db: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        query: str = "",
        scope: str | None = None,
        limit: int = 8,
    ) -> list[MemoryItem]:
        visibility, workspace_visibility = _visible_predicates(
            workspace_id=workspace_id, session_id=session_id,
            run_id=run_id, user_id=user_id)
        stmt = select(MemoryItem).where(
            MemoryItem.tenant_id == tenant_id,
            MemoryItem.deleted_at == None,
            or_(MemoryItem.expires_at == None, MemoryItem.expires_at > datetime.utcnow()),
            visibility,
            workspace_visibility,
        )
        if scope:
            stmt = stmt.where(MemoryItem.scope == scope)
        rows = (await db.execute(stmt)).scalars().all()
        if query:
            rows = [row for row in rows if _score(row, query) > 0]
            rows.sort(key=lambda item: _score(item, query), reverse=True)
        else:
            rows.sort(key=lambda item: (item.pinned, item.updated_at), reverse=True)
        return rows[: max(1, min(limit, 20))]

    @staticmethod
    async def get_visible(
        db: AsyncSession,
        *,
        tenant_id: str,
        memory_id: str,
        workspace_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
    ) -> MemoryItem | None:
        visibility, workspace_visibility = _visible_predicates(
            workspace_id=workspace_id, session_id=session_id,
            run_id=run_id, user_id=user_id)
        return await db.scalar(select(MemoryItem).where(
            MemoryItem.id == memory_id,
            MemoryItem.tenant_id == tenant_id,
            MemoryItem.deleted_at == None,
            visibility,
            workspace_visibility,
        ))

    @staticmethod
    async def update(db: AsyncSession, item: MemoryItem, **changes) -> MemoryItem:
        for key in ("title", "content", "confidence", "pinned", "expires_at"):
            if key in changes and changes[key] is not None:
                setattr(item, key, changes[key])
        item.updated_at = datetime.utcnow()
        await db.flush()
        return item

    @staticmethod
    async def forget(db: AsyncSession, *, tenant_id: str, memory_id: str,
                     workspace_id: str | None = None, session_id: str | None = None,
                     run_id: str | None = None, user_id: str | None = None) -> bool:
        item = await MemoryService.get_visible(
            db, tenant_id=tenant_id, memory_id=memory_id,
            workspace_id=workspace_id, session_id=session_id,
            run_id=run_id, user_id=user_id)
        if not item:
            return False
        item.deleted_at = datetime.utcnow()
        await db.flush()
        return True

    @staticmethod
    async def get_session_summary(db: AsyncSession, *, tenant_id: str,
                                  session_id: str) -> SessionSummary | None:
        return await db.scalar(select(SessionSummary).where(
            SessionSummary.tenant_id == tenant_id,
            SessionSummary.session_id == session_id,
        ))

    @staticmethod
    async def update_session_summary(
        db: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        session_id: str,
        messages: Iterable[dict],
    ) -> SessionSummary:
        rows = list(messages)
        existing = await MemoryService.get_session_summary(
            db, tenant_id=tenant_id, session_id=session_id)
        text_lines = []
        if existing and existing.summary:
            text_lines.append(existing.summary)
        for msg in rows[-12:]:
            role = msg.get("role", "")
            content = msg.get("content") or {}
            text = content.get("text", "") if isinstance(content, dict) else str(content)
            if text:
                text_lines.append(f"{role}: {text[:500]}")
        summary = "\n".join(text_lines)
        if len(summary) > 3000:
            summary = summary[-3000:]
        row = existing or SessionSummary(
            tenant_id=tenant_id, workspace_id=workspace_id, session_id=session_id)
        row.workspace_id = workspace_id
        row.summary = summary
        row.last_message_id = rows[-1].get("id", "") if rows else row.last_message_id
        row.updated_at = datetime.utcnow()
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def capture_candidates(
        db: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        session_id: str,
        run_id: str,
        user_id: str,
        messages: Iterable[dict],
    ) -> list[MemoryItem]:
        created = []
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content") or {}
            text = content.get("text", "") if isinstance(content, dict) else str(content)
            if len(text) < 12 or SECRET_RE.search(text):
                continue
            if any(marker in text for marker in ("记住", "以后", "偏好", "决定", "使用", "喜欢")):
                item = await MemoryService.write(
                    db,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    session_id=session_id,
                    run_id=run_id,
                    user_id=user_id,
                    scope="workspace",
                    kind="note",
                    title=text[:40],
                    content=text,
                    source_type="user",
                    source_id=msg.get("id", ""),
                    confidence=0.55,
                )
                created.append(item)
        return created

    @staticmethod
    def format_for_prompt(items: list[MemoryItem], summary: SessionSummary | None) -> str:
        parts = []
        if summary and summary.summary:
            parts.append("## Session Summary\n" + summary.summary)
        if items:
            lines = [
                f"- [{m.scope}/{m.kind}] {m.title}: {m.content}"
                for m in items
            ]
            parts.append("## Relevant Long-Term Memory\n" + "\n".join(lines))
        return "\n\n".join(parts)
