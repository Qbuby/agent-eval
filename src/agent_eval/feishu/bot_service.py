"""飞书机器人的会话处理主逻辑：身份绑定 + JWT 签发 + 编排入口。

一条飞书消息的处理流程（`handle_text`）：

1. 按 sender open_id 查已绑定 user。
2. 未绑定 → 进「等待入口码」态：
   - 若本条消息本身是有效入口码 → 按码（tenant + role）新建 user 并绑定
     open_id，回「绑定成功」。
   - 否则回「请发送入口码完成绑定」。
3. 已绑定 → 为该 user 签发短期 JWT，交给 orchestrator 编排（M4 接入；
   M3 阶段先回一句「已绑定为 <user>，可以开始使用」占位）。

租户隔离：机器人在常驻进程里（无 HTTP 请求上下文），DB 操作前必须手动
`set_tenant_context` + `set_role_context`，且 try/finally reset，否则跨租户
泄露或 ContextVar 泄漏到下一条消息。绑定查询走 users 表（不挂 TenantMixin，
不受过滤），但新建 user / 后续编排需要正确的租户上下文。

username 派生：飞书用户没有系统用户名，用 ``feishu_<open_id 前 12 位>`` 派生，
配唯一约束；冲突时追加后缀。email 用占位（feishu 用户可能没绑邮箱）。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from agent_eval.auth.security import create_access_token, hash_password
from agent_eval.db import async_session_factory
from agent_eval.db_models.repository import Repository
from agent_eval.db_models.tables import EntryCodeRow, UserRow
from agent_eval.db_models.tenant_context import (
    TenantContext,
    reset_tenant_context,
    set_tenant_context,
)

logger = logging.getLogger(__name__)


@dataclass
class BindResult:
    """一次消息处理的结果，供 service 层决定回帖内容。"""
    reply: str
    user_id: uuid.UUID | None = None
    role: str | None = None
    tenant_id: uuid.UUID | None = None
    token: str | None = None
    bound: bool = False


class FeishuBotService:
    """会话状态 + 绑定 + JWT。无状态持久化——会话态只在内存（进程重启即失忆，
    对绑定无影响：绑定是持久化的，只有「正在等码」这个瞬态会丢，用户重发即可）。"""

    def __init__(self) -> None:
        # 已知未绑定、正在等待入口码的 open_id（纯提示用，非安全边界）。
        self._awaiting_code: set[str] = set()
        # 危险动作二次确认：open_id -> (PendingAction, token)。用户下条消息回
        # 「确认」才执行。内存态，进程重启即清空（未确认的危险动作自然作废，
        # 这对删除类操作是安全的默认——宁可让用户重发，也不留悬空的删除意图）。
        self._pending: dict[str, tuple[Any, str]] = {}

    # 确认/取消的判定词。命中「确认」才执行；「取消」或其它任意输入都视为放弃，
    # 放弃后该条消息不再当新指令处理（避免「取消」被 LLM 二次解读），只回执一句。
    _CONFIRM_WORDS = {"确认", "确定", "yes", "y", "确认执行", "执行", "ok", "好"}
    _CANCEL_WORDS = {"取消", "cancel", "no", "n", "算了", "放弃"}

    async def _load_history(self, user: UserRow, open_id: str, limit: int = 20) -> list[dict[str, str]]:
        """取该用户最近对话历史，转成 messages 片段。租户上下文只窄包这次读取——
        不能外扩到 orchestration，否则会过滤掉 internal 租户的编排 provider。"""
        ctx = TenantContext(tenant_id=user.tenant_id, superadmin=bool(user.is_superadmin))
        ctx_token = set_tenant_context(ctx)
        try:
            async with async_session_factory() as session:
                repo = Repository(session)
                rows = await repo.get_recent_feishu_messages(open_id, limit=limit)
        except Exception as e:  # noqa: BLE001
            logger.warning("feishu history load failed: %s", e)
            return []
        finally:
            reset_tenant_context(ctx_token)
        return [{"role": r.role, "content": r.content} for r in rows]

    async def _save_turn(
        self, user: UserRow, open_id: str, user_text: str, assistant_text: str,
    ) -> None:
        """把一轮 user / assistant 文本各存一行。失败只告警、不阻断回复。
        租户上下文同样只窄包写入，靠 before_flush 盖章 tenant_id。"""
        ctx = TenantContext(tenant_id=user.tenant_id, superadmin=bool(user.is_superadmin))
        ctx_token = set_tenant_context(ctx)
        try:
            async with async_session_factory() as session:
                repo = Repository(session)
                await repo.add_feishu_message(open_id, "user", user_text)
                await repo.add_feishu_message(open_id, "assistant", assistant_text)
                await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("feishu history save failed: %s", e)
        finally:
            reset_tenant_context(ctx_token)

    async def handle_text(
        self, open_id: str, text: str, images: list[str] | None = None,
    ) -> BindResult:
        """处理一条消息，返回该回什么 + （已绑定时）user 上下文 + JWT。
        ``images`` 是 data URL 列表（base64 图片），有图时进多模态编排。"""
        text = (text or "").strip()
        images = images or []
        async with async_session_factory() as session:
            repo = Repository(session)
            user = await repo.get_user_by_feishu_open_id(open_id)

            if user is not None:
                # 已绑定：签发 JWT，交编排。空消息 / 招呼语给个引导，不进编排。
                token = create_access_token(user.id, user.role, user.tenant_id)
                self._awaiting_code.discard(open_id)

                def _bound(reply: str) -> BindResult:
                    return BindResult(
                        reply=reply,
                        user_id=user.id, role=user.role, tenant_id=user.tenant_id,
                        token=token, bound=True,
                    )

                # ── 二次确认门：有 pending 时，本条消息只用于确认/取消 ──
                # 优先于编排：处于确认态时，用户任何输入都不再当新指令解读，
                # 避免「取消」「确认」被 LLM 二次解释而误触发别的操作。
                pending_entry = self._pending.pop(open_id, None)
                if pending_entry is not None:
                    pending, _saved_token = pending_entry
                    lowered = text.lower()
                    if lowered in self._CONFIRM_WORDS:
                        from agent_eval.feishu.orchestrator import execute_pending
                        try:
                            # 用当前消息新签发的 token 执行（权限以此刻为准，
                            # 而非发起确认时——期间若被降权，应以新权限为准）。
                            result = await execute_pending(pending, token)
                        except Exception as e:  # noqa: BLE001
                            logger.exception("feishu execute_pending failed: %s", e)
                            result = f"执行出错了：{e}"
                        return _bound(result)
                    if lowered in self._CANCEL_WORDS:
                        return _bound(f"已取消：{pending.tool_name}。")
                    # 既非确认也非取消：放弃该 pending，并提示用户重新发起。
                    return _bound(
                        f"已放弃待确认的操作（{pending.tool_name}）。"
                        "如需删除请重新说明，我会再次向你确认。"
                    )

                # 无文本且无图：给个引导，不进编排。
                if not text and not images:
                    return _bound(
                        f"已绑定为 {user.username}（{user.role}）。"
                        "试试：列出所有数据集 / 查看评估器 / 最近的评估运行。"
                    )

                # 读最近历史（窄租户上下文），注入本轮编排以支持多轮记忆。
                history = await self._load_history(user, open_id)

                # 交给内置 agent 编排：自然语言 → 工具调用 → 汇总回复。
                from agent_eval.feishu.orchestrator import run_orchestration
                try:
                    outcome = await run_orchestration(
                        text, token=token, open_id=open_id,
                        history=history, images=(images or None),
                        user_name=user.display_name,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.exception("feishu orchestration failed: %s", e)
                    return _bound(f"处理出错了：{e}")

                # 命中危险工具：暂存 pending，回确认提示，等用户下条「确认」。
                # 危险确认链路不入历史（避免"确认/取消"污染上下文）。
                if outcome.pending is not None:
                    self._pending[open_id] = (outcome.pending, token)
                    return _bound(outcome.reply)

                # 正常完成：落历史（纯图无字幕存占位），再回复。
                user_hist = text or ("[图片]" if images else "")
                await self._save_turn(user, open_id, user_hist, outcome.reply)
                return _bound(outcome.reply)

            # 未绑定：尝试把本条消息当入口码。
            code = text
            entry = None
            if code:
                entry = (await session.execute(
                    select(EntryCodeRow).where(EntryCodeRow.code == code)
                )).scalar_one_or_none()

            if entry is None or not entry.is_active:
                self._awaiting_code.add(open_id)
                return BindResult(
                    reply="你还未绑定账号。请发送入口码完成绑定（向管理员获取）。",
                )

            # 有效入口码：按 code 的 tenant + role 新建 user 并绑定 open_id。
            new_user = await self._create_bound_user(session, entry, open_id)
            await session.commit()
            self._awaiting_code.discard(open_id)
            token = create_access_token(new_user.id, new_user.role, new_user.tenant_id)
            return BindResult(
                reply=f"绑定成功，账号 {new_user.username}（{new_user.role}）。可以开始使用。",
                user_id=new_user.id, role=new_user.role, tenant_id=new_user.tenant_id,
                token=token, bound=True,
            )

    async def _create_bound_user(
        self, session, entry: EntryCodeRow, open_id: str,
        union_id: str | None = None,
    ) -> UserRow:
        """按入口码（tenant + role）新建 user 并绑定 open_id（+ union_id）。

        新建 user 前设置租户上下文（before_flush 会给挂 TenantMixin 的行盖章；
        users 表本身不挂，但保持上下文一致、且后续编排复用）。username 从
        open_id 派生并去重；display_name 拉飞书昵称（失败留空，不阻断绑定）。"""
        base_username = f"feishu_{open_id[:12]}"
        username = base_username
        suffix = 1
        while await self._username_taken(session, username):
            suffix += 1
            username = f"{base_username}_{suffix}"

        display_name = await self._fetch_display_name(open_id)

        # 占位随机密码（飞书用户不走密码登录，但列 NOT NULL）。
        placeholder_pw = hash_password(uuid.uuid4().hex)
        user = UserRow(
            username=username,
            email=f"{username}@feishu.local",
            hashed_password=placeholder_pw,
            role=entry.role,
            tenant_id=entry.tenant_id,
            is_superadmin=False,
            feishu_open_id=open_id,
            feishu_union_id=union_id,
            display_name=display_name,
        )
        session.add(user)
        await session.flush()
        logger.info(
            "feishu bind: open_id=%s union_id=%s -> user=%s role=%s tenant=%s (code=%s)",
            open_id, union_id, username, entry.role, entry.tenant_id, entry.code,
        )
        return user

    @staticmethod
    async def _username_taken(session, username: str) -> bool:
        row = (await session.execute(
            select(UserRow.id).where(UserRow.username == username)
        )).scalar_one_or_none()
        return row is not None


_bot_service: FeishuBotService | None = None


def get_bot_service() -> FeishuBotService:
    global _bot_service
    if _bot_service is None:
        _bot_service = FeishuBotService()
    return _bot_service
