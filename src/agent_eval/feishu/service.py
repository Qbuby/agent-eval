"""飞书机器人长连接服务（lark-oapi ws.Client）。

生命周期照 SchedulerService 范式：`start()` 拉起、`stop()` 收尾，由
FastAPI lifespan 调度。长连接在后台 asyncio task 里跑，不阻塞事件循环。

关键事实（已 introspect lark-oapi 确认）：
- `ws.Client(app_id, app_secret, event_handler=..., auto_reconnect=True)`。
- `ws.Client.start()` 是**同步阻塞**（内部 `asyncio.run`），在已有事件循环的
  lifespan 里不能直接调；改在后台 task 里 `await client._connect()`（async），
  `auto_reconnect=True` 时 SDK 内部自愈重连。
- 事件用 `EventDispatcherHandler.builder("","").register_p2_im_message_receive_v1(fn)`
  注册；回调签名 `fn(data: P2ImMessageReceiveV1)`，**同步函数**（SDK 在自己的
  线程/循环里调），故回调内用 `asyncio.run_coroutine_threadsafe` 把真正的异步
  处理丢回主事件循环，避免跨循环踩坑。
- 发消息用 `lark.Client.builder().app_id().app_secret().build()` 的 im v1
  `create` API（reply 用 reply API）。

未配置（enabled=False 或缺凭证）时 `start()` 直接跳过，backend 正常启动。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent_eval.config import settings

logger = logging.getLogger(__name__)


class FeishuBotService:
    """飞书长连接的持有者与生命周期管理。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._ws_client: Any = None
        self._lark_client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False

    async def start(self) -> None:
        if not settings.feishu.configured:
            logger.info("feishu bot disabled or unconfigured; skipping ws start")
            return
        try:
            import lark_oapi as lark
        except ImportError:
            logger.warning("lark-oapi not installed; feishu bot unavailable")
            return

        self._loop = asyncio.get_running_loop()

        # 发消息用的 API client（app 身份）。复用共享单例，Bitable/主动推送
        # 与本服务共用同一 client。
        from agent_eval.feishu.client import get_lark_client
        self._lark_client = get_lark_client()

        # 事件分发：只订阅「接收消息」。回调是同步的，内部把异步处理丢回主循环。
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message_sync)
            .build()
        )
        self._ws_client = lark.ws.Client(
            settings.feishu.app_id,
            settings.feishu.app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
            auto_reconnect=True,
        )

        # 后台 task 跑 async 连接；start() 是阻塞版，这里用内部 _connect 协程。
        self._task = asyncio.create_task(self._run(), name="feishu-ws")
        self._started = True
        logger.info("feishu bot ws service started (app_id=%s)", settings.feishu.app_id)

    async def _run(self) -> None:
        try:
            await self._ws_client._connect()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("feishu ws connection loop crashed: %s", e)

    def _on_message_sync(self, data: Any) -> None:
        """SDK 同步回调。把真正的处理丢回主事件循环（异步、可访问 DB）。"""
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._handle_message(data), self._loop)
        except Exception as e:  # noqa: BLE001
            logger.warning("failed to dispatch feishu message to loop: %s", e)

    async def _handle_message(self, data: Any) -> None:
        """处理一条飞书消息：交 bot_service 做绑定/编排，回帖其结果。
        M3：绑定 + JWT；M4：已绑定用户的消息进 orchestrator 编排。"""
        try:
            event = data.event
            msg = event.message
            open_id = event.sender.sender_id.open_id
            # union_id 事件里直接带（跨应用稳定身份键，零 API 成本）；老 SDK 可能无此属性。
            union_id = getattr(event.sender.sender_id, "union_id", None)
            text, image_keys = self._extract_message(msg)
            logger.info(
                "feishu message from %s: %s (images=%d)", open_id, text, len(image_keys)
            )

            # 下载图片 → base64 data URL 列表。单张失败只跳过该张，不阻断问答。
            images: list[str] = []
            for key in image_keys:
                url = await self._download_image_data_url(msg.message_id, key)
                if url:
                    images.append(url)

            from agent_eval.feishu.bot_service import get_bot_service
            bot = get_bot_service()
            result = await bot.handle_text(
                open_id, text, images=images or None, union_id=union_id,
            )
            await self._reply(msg.message_id, result.reply)
        except Exception as e:  # noqa: BLE001
            logger.exception("feishu message handling failed: %s", e)
            try:
                await self._reply(data.event.message.message_id, "处理消息时出错，请稍后重试。")
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _extract_message(msg: Any) -> tuple[str, list[str]]:
        """从消息体抽 (文本, 图片 image_key 列表)。

        - text：content = `{"text": "..."}`
        - image：content = `{"image_key": "..."}`（单图消息）
        - post（富文本）：content = `{"title", "content": [[节点...]]}`，遍历二维
          数组，`tag=="text"` 拼文本、`tag=="img"` 收 image_key（可多张）。
        其它类型返回空。任何解析异常都吞掉返回已抽到的部分（不阻断问答）。
        """
        import json
        text = ""
        image_keys: list[str] = []
        try:
            mtype = msg.message_type
            content = json.loads(msg.content) or {}
            if mtype == "text":
                text = (content.get("text") or "").strip()
            elif mtype == "image":
                key = content.get("image_key")
                if key:
                    image_keys.append(key)
            elif mtype == "post":
                parts: list[str] = []
                for line in content.get("content") or []:
                    for node in line or []:
                        tag = node.get("tag")
                        if tag == "text":
                            parts.append(node.get("text") or "")
                        elif tag == "img":
                            key = node.get("image_key")
                            if key:
                                image_keys.append(key)
                text = "".join(parts).strip()
        except Exception:  # noqa: BLE001
            logger.warning("failed to parse feishu message content", exc_info=True)
        return text, image_keys

    async def _download_image_data_url(self, message_id: str, image_key: str) -> str | None:
        """下载消息里的图片资源，返回 base64 data URL；失败返回 None（不抛）。

        复用共享 lark client（app 身份，无需 user OAuth）。同步 SDK 调用丢线程池，
        避免阻塞事件循环。仿 bitable 的 RequestOption/success 校验范式。
        """
        client = self._lark_client or get_lark_client()
        if client is None:
            logger.warning("lark client unavailable, cannot download image")
            return None
        try:
            from lark_oapi.api.im.v1 import GetMessageResourceRequest

            req = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(image_key)
                .type("image")
                .build()
            )

            def _call() -> Any:
                return client.im.v1.message_resource.get(req)

            resp = await asyncio.to_thread(_call)
            if resp is None or not getattr(resp, "success", lambda: False)():
                logger.warning(
                    "download feishu image failed (key=%s): code=%s msg=%s",
                    image_key, getattr(resp, "code", None), getattr(resp, "msg", None),
                )
                return None
            raw = resp.file.read()
            import base64

            b64 = base64.b64encode(raw).decode("ascii")
            # 飞书图片多为 png/jpeg；用 image/png 作 data URL 类型对视觉模型足够通用。
            return f"data:image/png;base64,{b64}"
        except Exception:  # noqa: BLE001
            logger.warning("download feishu image crashed (key=%s)", image_key, exc_info=True)
            return None

    @staticmethod
    def _build_card(text: str) -> dict[str, Any]:
        """把回复文本包成飞书交互卡片（markdown 渲染），比纯文本清晰。

        - 头部模板按内容选色：待确认的危险操作用橙色 + ⚠️ 标题，醒目区分；
          其余用蓝色常规标题。判定只看文本特征（编排层对危险确认已注入
          「需要确认」字样），不扩 handle_text 的字符串契约。
        - 正文用 lark_md（支持 **加粗** / 链接 / 换行），兼容性最稳。
        """
        danger = "需要确认" in text or "不可逆" in text
        if danger:
            template, title = "orange", "⚠️ Agent-Eval · 待确认"
        else:
            template, title = "blue", "Agent-Eval"
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": text}},
            ],
        }

    async def _reply(self, message_id: str, text: str) -> None:
        """回复指定消息，用交互卡片（markdown）。lark im v1 reply API
        （同步 SDK，跑在线程池里）。卡片构建/发送失败时退回纯文本，保证有回声。"""
        if self._lark_client is None:
            return
        import json

        import lark_oapi as lark  # noqa: F401
        from lark_oapi.api.im.v1 import (
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        def _send(content: str, msg_type: str) -> None:
            req = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .content(content)
                    .msg_type(msg_type)
                    .build()
                )
                .build()
            )
            # SDK 的 im.v1.message.reply 是同步阻塞调用，丢线程池避免卡事件循环。
            self._lark_client.im.v1.message.reply(req)

        try:
            card = self._build_card(text)
            await asyncio.to_thread(
                _send, json.dumps(card, ensure_ascii=False), "interactive"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("feishu card reply failed, falling back to text: %s", e)
            try:
                await asyncio.to_thread(
                    _send, json.dumps({"text": text}, ensure_ascii=False), "text"
                )
            except Exception as e2:  # noqa: BLE001
                logger.exception("feishu reply failed: %s", e2)

    async def send_card(
        self, receive_id: str, text: str, *, receive_id_type: str = "open_id"
    ) -> bool:
        """主动给用户/群推一张卡片（不依赖 incoming message_id）。

        与 ``_reply`` 的区别：用 im v1 **create** API（reply 需 message_id、会
        过期，不能用于定时通知）。receive_id_type 支持 open_id / chat_id 等。
        同步 SDK 丢线程池，卡片失败退回纯文本；全程 best-effort，返回是否送达，
        绝不抛出——通知失败不应影响调用方（评估落库、定时任务）。

        service 是单例，后台任务可直接 ``get_service().send_card(open_id, text)``。
        """
        # 用共享单例：本服务未 start（如纯后台通知场景）时 _lark_client 为 None，
        # 回退到共享 client，保证通知能力不依赖 ws 长连接是否已拉起。
        client = self._lark_client
        if client is None:
            from agent_eval.feishu.client import get_lark_client
            client = get_lark_client()
        if client is None:
            logger.info("feishu send_card skipped: no lark client (unconfigured)")
            return False

        import json

        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        def _send(content: str, msg_type: str) -> None:
            req = (
                CreateMessageRequest.builder()
                .receive_id_type(receive_id_type)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .content(content)
                    .msg_type(msg_type)
                    .build()
                )
                .build()
            )
            client.im.v1.message.create(req)

        try:
            card = self._build_card(text)
            await asyncio.to_thread(
                _send, json.dumps(card, ensure_ascii=False), "interactive"
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("feishu card push failed, falling back to text: %s", e)
            try:
                await asyncio.to_thread(
                    _send, json.dumps({"text": text}, ensure_ascii=False), "text"
                )
                return True
            except Exception as e2:  # noqa: BLE001
                logger.exception("feishu send_card failed: %s", e2)
                return False

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self._ws_client = None
        self._lark_client = None
        self._started = False
        logger.info("feishu bot ws service stopped")


_service: FeishuBotService | None = None


def get_service() -> FeishuBotService:
    global _service
    if _service is None:
        _service = FeishuBotService()
    return _service
