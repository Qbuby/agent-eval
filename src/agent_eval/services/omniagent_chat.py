"""OmniAgent 多会话对话的服务层：消费上游 SSE、标准化事件、落库收口。

职责边界
--------
router 只做鉴权、参数校验和行的创建/查询；**所有与 OmniAgent 的交互和消息行的
终态写入都在这里**，因为这两件事必须成对发生：流一旦开始，无论正常结束、上游
报错、还是浏览器断开，都得把 assistant 消息从 ``streaming`` 推进到终态并清掉
会话的 ``active_message_id``（单飞门禁），否则该会话会永久卡住不能再发消息。

上游协议（与 evaluation/agent_adapter.py 的 ``langgraph_v2`` 同一口径）
----------------------------------------------------------------------
- 请求体是 ``extra="forbid"`` 的严格 schema，**只接受** ``question`` 与
  ``configurable``。多写任何顶层字段（历史上的 ``stream: true``）都会 422
  extra_forbidden；流式靠 ``Accept: text/event-stream`` 协商，用户身份走
  ``X-User-Id`` header。
- 响应是 LangChain ``astream_events v2`` 事件，混发 OmniAgent 自造的控制帧
  （``{"status": "error"}`` / ``interrupted`` / ``enqueued``）与协议帧
  （``command_result`` / ``structured_output``）。控制帧的要害是 **HTTP 200 早已
  发出**，服务端异常只能在流里发现。

对外事件（前端 services/omniagent.ts 的判别联合）
-------------------------------------------------
``message_start`` → ``content_delta`` / ``tool_start`` / ``tool_end`` /
``structured_output``（任意顺序、任意条数）→ ``done``；出错时在 ``done`` 之前
发一条 ``error``。每帧都带 ``message_id``，前端按它定位气泡。

消息终态：``completed`` | ``failed`` | ``cancelled``（``streaming`` 只是流进行
中的中间态，落库后不会长期停留在此）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select, update

from agent_eval.config import settings
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import (
    OmniAgentChatMessageRow,
    OmniAgentChatSessionRow,
    UserRow,
)
from agent_eval.db_models.tenant_context import INTERNAL_TENANT_ID
from agent_eval.omniagent_runtime.events import append_event
from agent_eval.omniagent_runtime.security import (
    execution_enabled_for_tenant,
    mint_execution_token,
)

logger = logging.getLogger(__name__)

# 消息状态取值。前端 OmniAgentMessageStatus 与此一一对应。
STATUS_STREAMING = "streaming"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

# 默认让 agent 用中文回复；与评测侧 SSEStreamAdapter 的默认一致。
DEFAULT_LANGUAGE = "请用中文回复"

# The token exposes only reviewed capability families. Individual endpoints still
# enforce their own scope and owner/tenant filters.
EXECUTION_SCOPES = (
    "data:search",
    "data:describe",
    "data:query",
    "artifact:search",
    "artifact:materialize",
    "artifact:publish",
    "analysis:submit",
    "job:read",
    "job:cancel",
    "action:prepare",
    "action:read",
    "schedule:read",
    "memory:search",
)

# 收尾任务的强引用集合。客户端断开时收尾要 fire-and-forget（异步生成器在
# GeneratorExit 之后不能再 await 业务逻辑），若不持引用会被 GC 掉，消息永远停在
# streaming、会话的单飞门禁永远不放开。
_pending_finalizers: set[asyncio.Task] = set()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def content_to_text(value: Any) -> str:
    """把 JSONB 里的 content 投影成纯字符串。

    契约是「对前端只输出纯字符串」，但 JSONB 列历史上/未来都可能存下数组或对象
    （多模态 content blocks）。这里统一收敛：字符串原样，列表按 text 块拼接，
    其余 JSON 序列化，绝不把 ``None``/``dict`` 泄给前端的 ``content: string``。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def sse_frame(event: dict[str, Any]) -> str:
    """序列化成一个 SSE 事件块。

    同时写 ``event:`` 行与 ``data:`` 的 ``type`` 字段：前端以 data.type 为准
    （见 services/omniagent.ts 的 parseSseBlock），``event:`` 行只是给
    curl / 浏览器 DevTools 看的冗余信息。data 里不能有裸换行，故 json.dumps
    不加缩进。
    """
    payload = json.dumps(event, ensure_ascii=False, default=str)
    return f"event: {event.get('type', 'message')}\ndata: {payload}\n\n"


def _delta_from_chunk(data: dict[str, Any]) -> str:
    """从 ``on_chat_model_stream`` 事件里取出这一帧的文本增量。"""
    chunk = data.get("chunk")
    if not isinstance(chunk, dict):
        return ""
    kwargs = chunk.get("kwargs") or {}
    content = kwargs.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def utcnow() -> datetime:
    return _utcnow()


def owner_scope(user: UserRow | None) -> tuple[uuid.UUID, uuid.UUID | None]:
    """返回必须显式写进查询的租户与用户范围。"""
    if user is None:
        return INTERNAL_TENANT_ID, None
    return user.tenant_id, user.id


def owner_clause(model, user: UserRow | None) -> tuple[Any, Any]:
    tenant_id, owner_id = owner_scope(user)
    owner_filter = model.created_by.is_(None) if owner_id is None else model.created_by == owner_id
    return model.tenant_id == tenant_id, owner_filter


async def get_owned_session(
    db,
    session_id: uuid.UUID,
    user: UserRow | None,
    *,
    lock: bool = False,
) -> OmniAgentChatSessionRow:
    stmt = select(OmniAgentChatSessionRow).where(
        OmniAgentChatSessionRow.id == session_id,
        *owner_clause(OmniAgentChatSessionRow, user),
        OmniAgentChatSessionRow.deleted_at.is_(None),
    )
    if lock:
        stmt = stmt.with_for_update()
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return row


def session_dict(row: OmniAgentChatSessionRow, message_count: int = 0) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "thread_id": row.thread_id,
        "title": row.title,
        "title_source": row.title_source,
        "message_count": message_count,
        "active_message_id": str(row.active_message_id) if row.active_message_id else None,
        "last_message_at": row.last_message_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def message_dict(row: OmniAgentChatMessageRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "session_id": str(row.session_id),
        "sequence": row.sequence,
        "role": row.role,
        "content": content_to_text(row.content),
        "status": row.status,
        "tool_calls": row.tool_calls,
        "structured_output": row.structured_output,
        "error": row.error,
        "retry_of_message_id": str(row.retry_of_message_id) if row.retry_of_message_id else None,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def recover_stale_generation(db, row: OmniAgentChatSessionRow) -> bool:
    """只回收超过上游超时窗口的僵尸流，不干扰仍在进行的请求。"""
    if not row.active_message_id:
        return False
    active = (await db.execute(
        select(OmniAgentChatMessageRow).where(
            OmniAgentChatMessageRow.id == row.active_message_id,
            OmniAgentChatMessageRow.session_id == row.id,
        )
    )).scalar_one_or_none()
    if active is None or active.status != STATUS_STREAMING:
        row.active_message_id = None
        return True
    age = (_utcnow() - active.updated_at).total_seconds()
    if age <= settings.omniagent.timeout + 60:
        return False
    active.status = STATUS_CANCELLED
    active.error = "服务重启或流超时，生成已取消"
    active.updated_at = _utcnow()
    row.active_message_id = None
    row.updated_at = _utcnow()
    return True


def _auto_title(text: str) -> str:
    normalized = " ".join(text.split())
    return normalized[:48] + ("…" if len(normalized) > 48 else "")


async def begin_generation(
    session_id: uuid.UUID,
    user: UserRow | None,
    *,
    text: str | None = None,
    retry_of: uuid.UUID | None = None,
) -> tuple[
    OmniAgentChatSessionRow,
    OmniAgentChatMessageRow | None,
    OmniAgentChatMessageRow,
    str,
]:
    """事务内抢占单飞门禁并创建消息行。"""
    tenant_id, owner_id = owner_scope(user)
    async with async_session_factory() as db:
        row = await get_owned_session(db, session_id, user, lock=True)
        await recover_stale_generation(db, row)
        if row.active_message_id:
            raise HTTPException(status_code=409, detail="该会话正在生成回复")

        max_sequence = int((await db.execute(
            select(func.coalesce(func.max(OmniAgentChatMessageRow.sequence), 0)).where(
                OmniAgentChatMessageRow.session_id == session_id,
                OmniAgentChatMessageRow.tenant_id == tenant_id,
            )
        )).scalar_one())

        user_message: OmniAgentChatMessageRow | None = None
        if retry_of is None:
            if text is None:
                raise HTTPException(status_code=400, detail="消息不能为空")
            user_message = OmniAgentChatMessageRow(
                tenant_id=tenant_id,
                session_id=session_id,
                sequence=max_sequence + 1,
                role="user",
                content=text,
                status=STATUS_COMPLETED,
            )
            question = text
            assistant_sequence = max_sequence + 2
            db.add(user_message)
            if row.title_source == "auto" and row.title == "新对话":
                row.title = _auto_title(text)
        else:
            failed = (await db.execute(
                select(OmniAgentChatMessageRow).where(
                    OmniAgentChatMessageRow.id == retry_of,
                    OmniAgentChatMessageRow.session_id == session_id,
                    OmniAgentChatMessageRow.tenant_id == tenant_id,
                    OmniAgentChatMessageRow.role == "assistant",
                    OmniAgentChatMessageRow.status.in_([STATUS_FAILED, STATUS_CANCELLED]),
                )
            )).scalar_one_or_none()
            if failed is None:
                raise HTTPException(status_code=409, detail="仅失败或已取消的回复可重试")
            source = (await db.execute(
                select(OmniAgentChatMessageRow)
                .where(
                    OmniAgentChatMessageRow.session_id == session_id,
                    OmniAgentChatMessageRow.tenant_id == tenant_id,
                    OmniAgentChatMessageRow.role == "user",
                    OmniAgentChatMessageRow.sequence < failed.sequence,
                )
                .order_by(OmniAgentChatMessageRow.sequence.desc())
                .limit(1)
            )).scalar_one_or_none()
            if source is None:
                raise HTTPException(status_code=409, detail="找不到该回复对应的用户消息")
            question = content_to_text(source.content)
            assistant_sequence = max_sequence + 1

        assistant = OmniAgentChatMessageRow(
            tenant_id=tenant_id,
            session_id=session_id,
            sequence=assistant_sequence,
            role="assistant",
            content="",
            status=STATUS_STREAMING,
            retry_of_message_id=retry_of,
        )
        db.add(assistant)
        await db.flush()
        if settings.omniagent.product_plane_enabled:
            if user_message is not None:
                await append_event(
                    db,
                    tenant_id=tenant_id,
                    user_id=owner_id,
                    session_id=session_id,
                    message_id=user_message.id,
                    event_type="message.created",
                    entity_type="message",
                    entity_id=str(user_message.id),
                    payload={
                        "role": "user",
                        "status": STATUS_COMPLETED,
                        "sequence": user_message.sequence,
                    },
                )
            await append_event(
                db,
                tenant_id=tenant_id,
                user_id=owner_id,
                session_id=session_id,
                message_id=assistant.id,
                event_type="message.streaming",
                entity_type="message",
                entity_id=str(assistant.id),
                payload={
                    "role": "assistant",
                    "status": STATUS_STREAMING,
                    "sequence": assistant.sequence,
                    "retry_of_message_id": str(retry_of) if retry_of else None,
                },
            )
        row.active_message_id = assistant.id
        row.last_message_at = _utcnow()
        row.updated_at = _utcnow()
        await db.commit()
        await db.refresh(row)
        if user_message is not None:
            await db.refresh(user_message)
        await db.refresh(assistant)
        return row, user_message, assistant, question


def stream_upstream(
    row: OmniAgentChatSessionRow,
    user_message: OmniAgentChatMessageRow | None,
    assistant: OmniAgentChatMessageRow,
    question: str,
    user: UserRow | None,
) -> AsyncGenerator[str, None]:
    identity = f"agent-eval-user-{user.id}" if user is not None else "agent-eval-dev"
    service = OmniAgentChatService(
        session_id=row.id,
        assistant_message_id=assistant.id,
        thread_id=row.thread_id,
        question=question,
        user_identity=identity,
        user_message_id=user_message.id if user_message is not None else None,
        execution_user_id=user.id if user is not None else None,
        execution_tenant_id=user.tenant_id if user is not None else None,
        execution_role=user.role if user is not None else None,
    )
    return service.stream()


class OmniAgentChatService:
    """一次「发消息 / 重试」的完整生命周期。

    实例是 per-request 的（持有本次的 message_id、累积文本、工具调用），不要
    跨请求复用。
    """

    def __init__(
        self,
        *,
        session_id: uuid.UUID,
        assistant_message_id: uuid.UUID,
        thread_id: str,
        question: str,
        user_identity: str | None = None,
        user_message_id: uuid.UUID | None = None,
        execution_user_id: uuid.UUID | None = None,
        execution_tenant_id: uuid.UUID | None = None,
        execution_role: str | None = None,
        url: str | None = None,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.session_id = session_id
        self.assistant_message_id = assistant_message_id
        self.thread_id = thread_id
        self.question = question
        self.user_message_id = user_message_id
        self.execution_user_id = execution_user_id
        self.execution_tenant_id = execution_tenant_id
        self.execution_role = execution_role
        # X-User-Id 必须 latin-1 可编码（h11 硬要求），故不复用可能含中文的
        # thread_id / 用户名；调用方传的是用户 UUID 的字符串形式。
        self.user_identity = user_identity or f"agent-eval-{uuid.uuid4().hex[:16]}"
        self.url = url or settings.omniagent.internal_url
        self.timeout = timeout if timeout is not None else settings.omniagent.timeout
        self._client = client

        self._text: list[str] = []
        self._tool_calls: list[dict[str, Any]] = []
        # run_id -> 已下发 tool_start 的那条记录，供 tool_end 回填 output/耗时。
        self._active_tools: dict[str, dict[str, Any]] = {}
        self._structured_output: Any = None
        self._error: str | None = None

    # ── 请求构造 ──────────────────────────────────────────────────────────

    def build_payload(self) -> dict[str, Any]:
        """Build the strict upstream payload and optionally add turn-bound auth."""
        configurable: dict[str, Any] = {
            "thread_id": self.thread_id,
            "language": DEFAULT_LANGUAGE,
        }
        execution_allowed = (
            self.execution_tenant_id is not None
            and execution_enabled_for_tenant(self.execution_tenant_id)
        )
        if execution_allowed and self.execution_user_id is not None:
            if self.execution_tenant_id is None or self.execution_role is None:
                raise ValueError("incomplete execution identity")
            configurable["execution_auth"] = {
                "token": mint_execution_token(
                    user_id=self.execution_user_id,
                    tenant_id=self.execution_tenant_id,
                    role=self.execution_role,
                    session_id=self.session_id,
                    message_id=self.assistant_message_id,
                    scopes=EXECUTION_SCOPES,
                )
            }
        return {"question": self.question, "configurable": configurable}

    def build_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-User-Id": self.user_identity,
        }

    # ── 事件标准化 ────────────────────────────────────────────────────────

    def normalize(self, obj: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        """把一个上游 JSON 帧翻成 0..n 条对外事件，并给出「是否终止流」。

        终止只有两种来源：上游报错（``status: error``），以及 agent 停下等人工
        输入 / 被排队（``interrupted`` / ``enqueued``）—— 后两者不会再产出答案，
        继续读只是干等超时。
        """
        mid = str(self.assistant_message_id)

        # --- OmniAgent 控制帧（HTTP 200 之后才能发现的服务端故障） ---
        if obj.get("status") == "error":
            detail = obj.get("error")
            message = str(detail) if detail else "OmniAgent 服务端异常"
            self._error = message
            return [{"type": "error", "message_id": mid, "message": message}], True

        event = obj.get("event")
        if event in ("interrupted", "enqueued"):
            detail = obj.get("interrupt") if event == "interrupted" else obj.get("message")
            message = (
                "OmniAgent 正在等待人工输入，本次未产出回答"
                if event == "interrupted"
                else "该会话有未完成的中断，本次请求被排队"
            )
            if detail:
                message = f"{message}：{detail}"
            self._error = message
            return [{"type": "error", "message_id": mid, "message": message}], True

        # --- v1 协议帧 ---
        if event == "command_result":
            text = obj.get("text")
            if isinstance(text, str) and text:
                self._text.append(text)
                return [{"type": "content_delta", "message_id": mid, "delta": text}], False
            return [], False
        if event == "structured_output":
            self._structured_output = obj.get("structured_output")
            return [
                {
                    "type": "structured_output",
                    "message_id": mid,
                    "data": self._structured_output,
                }
            ], False

        # --- LangGraph astream_events v2 ---
        data = obj.get("data") or {}
        if event == "on_chat_model_stream":
            delta = _delta_from_chunk(data)
            if not delta:
                return [], False
            self._text.append(delta)
            return [{"type": "content_delta", "message_id": mid, "delta": delta}], False

        if event == "on_tool_start":
            run_id = str(obj.get("run_id") or uuid.uuid4())
            name = obj.get("name") or data.get("name") or ""
            record = {
                "id": run_id,
                "name": name,
                "input": data.get("input"),
                "output": None,
                "error": None,
                "duration_ms": None,
                "_started": time.perf_counter(),
            }
            self._active_tools[run_id] = record
            self._tool_calls.append(record)
            return [
                {
                    "type": "tool_start",
                    "message_id": mid,
                    "tool_call_id": run_id,
                    "name": name,
                    "input": record["input"],
                }
            ], False

        if event == "on_tool_end":
            run_id = str(obj.get("run_id") or "")
            record = self._active_tools.pop(run_id, None)
            output = data.get("output")
            # 任意对象都可能来（ToolMessage 等），非 JSON 原生类型截断成字符串，
            # 免得 JSONB 落库时炸掉整条消息。
            if not isinstance(output, (str, int, float, bool, dict, list, type(None))):
                output = str(output)[:2000]
            duration_ms: int | None = None
            if record is not None:
                duration_ms = int((time.perf_counter() - record["_started"]) * 1000)
                record["output"] = output
                record["duration_ms"] = duration_ms
            return [
                {
                    "type": "tool_end",
                    "message_id": mid,
                    "tool_call_id": run_id,
                    "output": output,
                    "error": None,
                    "duration_ms": duration_ms,
                }
            ], False

        return [], False

    # ── 主流程 ────────────────────────────────────────────────────────────

    async def stream(self) -> AsyncGenerator[str, None]:
        """产出 SSE 文本块。异常不外抛——一律翻成 error + done 帧。

        为什么不让异常冒泡：响应头在第一帧就发出去了，此后抛异常只会让浏览器
        看到一条断掉的流（前端 catch 到的是网络错误而非业务原因）。把故障写进
        流里，前端才能把这条消息标红并保留半截内容。
        """
        mid = str(self.assistant_message_id)
        start_event: dict[str, Any] = {"type": "message_start", "message_id": mid}
        if self.user_message_id is not None:
            start_event["user_message_id"] = str(self.user_message_id)
        yield sse_frame(start_event)

        status = STATUS_COMPLETED
        try:
            async for frame in self._consume_upstream():
                yield frame
            if self._error:
                status = STATUS_FAILED
        except (asyncio.CancelledError, GeneratorExit):
            # 客户端断开 / 任务被取消：尽力落 cancelled。此处不能再 await 业务
            # 逻辑（GeneratorExit 之后 await 会变成 RuntimeError），故把收尾丢给
            # 独立任务，并持引用防 GC。
            self._schedule_finalize(STATUS_CANCELLED)
            raise
        except httpx.HTTPStatusError as exc:
            body = ""
            try:
                body = exc.response.text[:500]
            except Exception:  # noqa: BLE001 - 流式响应可能尚未读取
                body = ""
            self._error = f"OmniAgent 返回 {exc.response.status_code}"
            if body:
                self._error = f"{self._error}: {body}"
            status = STATUS_FAILED
            yield sse_frame({"type": "error", "message_id": mid, "message": self._error})
        except Exception as exc:  # noqa: BLE001 - 任何上游故障都要收口成 error 帧
            logger.warning(
                "omniagent stream failed: session=%s message=%s err=%s",
                self.session_id, self.assistant_message_id, exc,
            )
            self._error = f"{type(exc).__name__}: {exc}"
            status = STATUS_FAILED
            yield sse_frame({"type": "error", "message_id": mid, "message": self._error})

        final_content = self.final_content()
        try:
            await self.finalize(status)
        except Exception as exc:  # noqa: BLE001 - 落库失败也要把流收干净
            logger.exception("omniagent finalize failed: %s", exc)

        yield sse_frame(
            {
                "type": "done",
                "message_id": mid,
                "final_content": final_content,
                "status": status,
            }
        )

    async def _consume_upstream(self) -> AsyncGenerator[str, None]:
        payload = self.build_payload()
        headers = self.build_headers()
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            async with client.stream(
                "POST", self.url, json=payload, headers=headers
            ) as resp:
                if resp.status_code >= 400:
                    # 流式响应默认未读，先 aread 再 raise，否则 .text 抛
                    # ResponseNotRead，真正的 422 detail 就丢了。
                    await resp.aread()
                    resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    events, stop = self.normalize(obj)
                    for event in events:
                        yield sse_frame(event)
                    if stop:
                        break
        finally:
            if own_client:
                await client.aclose()

    def final_content(self) -> str:
        return "".join(self._text)

    def persisted_tool_calls(self) -> list[dict[str, Any]] | None:
        """落库用的 tool_calls（去掉计时用的私有字段）。"""
        if not self._tool_calls:
            return None
        return [
            {key: value for key, value in call.items() if not key.startswith("_")}
            for call in self._tool_calls
        ]

    # ── 收口落库 ──────────────────────────────────────────────────────────

    async def finalize(self, status: str) -> None:
        """把 assistant 消息推进到终态，并放开会话的单飞门禁。

        两件事必须一起做：只写消息状态会让 ``active_message_id`` 永久占位，该
        会话再也发不出消息；只清门禁会留下永远 ``streaming`` 的僵尸消息。
        """
        content = self.final_content()
        async with async_session_factory() as db:
            await db.execute(
                update(OmniAgentChatMessageRow)
                .where(OmniAgentChatMessageRow.id == self.assistant_message_id)
                .values(
                    content=content,
                    status=status,
                    tool_calls=self.persisted_tool_calls(),
                    structured_output=self._structured_output,
                    error=self._error,
                    updated_at=_utcnow(),
                )
            )
            await db.execute(
                update(OmniAgentChatSessionRow)
                .where(
                    OmniAgentChatSessionRow.id == self.session_id,
                    OmniAgentChatSessionRow.active_message_id == self.assistant_message_id,
                )
                .values(active_message_id=None, last_message_at=_utcnow())
            )
            if settings.omniagent.product_plane_enabled:
                tool_calls = self.persisted_tool_calls() or []
                tool_names = list(dict.fromkeys(
                    str(call.get("name")) for call in tool_calls if call.get("name")
                ))[:20]
                await append_event(
                    db,
                    tenant_id=self.execution_tenant_id or INTERNAL_TENANT_ID,
                    user_id=self.execution_user_id,
                    session_id=self.session_id,
                    message_id=self.assistant_message_id,
                    event_type=f"message.{status}",
                    entity_type="message",
                    entity_id=str(self.assistant_message_id),
                    payload={
                        "role": "assistant",
                        "status": status,
                        "tool_call_count": len(tool_calls),
                        "tool_names": tool_names,
                        "has_structured_output": self._structured_output is not None,
                    },
                )
            await db.commit()

    def _schedule_finalize(self, status: str) -> None:
        task = asyncio.create_task(self._finalize_quietly(status))
        _pending_finalizers.add(task)
        task.add_done_callback(_pending_finalizers.discard)

    async def _finalize_quietly(self, status: str) -> None:
        try:
            await self.finalize(status)
        except Exception as exc:  # noqa: BLE001 - 后台收尾不能把异常抛进事件循环
            logger.warning(
                "omniagent cancelled-finalize failed: message=%s err=%s",
                self.assistant_message_id, exc,
            )


async def clear_stale_active_messages() -> int:
    """把上个进程残留的 ``streaming`` 消息收成 ``cancelled`` 并放开门禁。

    进程重启会让所有在途流凭空消失，消息永久停在 ``streaming``、会话永久被单飞
    门禁锁住。与 ``sweep_orphaned_runs`` 同一思路，供 lifespan 启动时调用。
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(OmniAgentChatMessageRow.id).where(
                OmniAgentChatMessageRow.status == STATUS_STREAMING
            )
        )
        ids = [row[0] for row in result.all()]
        if ids:
            await db.execute(
                update(OmniAgentChatMessageRow)
                .where(OmniAgentChatMessageRow.id.in_(ids))
                .values(status=STATUS_CANCELLED, updated_at=_utcnow())
            )
        await db.execute(
            update(OmniAgentChatSessionRow)
            .where(OmniAgentChatSessionRow.active_message_id.isnot(None))
            .values(active_message_id=None)
        )
        await db.commit()
        return len(ids)
