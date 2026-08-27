"""#148 验收专用多模态 SSE mock agent。

监听 8001；收到 canonical blocks 时在回答中返回 IMAGE_BLOCK_RECEIVED，并把
最后一次请求保存到 /last，便于验收同时证明 adapter 没把图片压成纯文本。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_lock = threading.Lock()
_last: dict = {}


def _has_image(question: object) -> bool:
    return isinstance(question, list) and any(
        isinstance(block, dict) and block.get("type") == "image"
        for block in question
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _json(self, code: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/last"):
            with _lock:
                self._json(200, dict(_last))
            return
        if self.path.startswith("/reset"):
            with _lock:
                _last.clear()
            self._json(200, {"reset": True})
            return
        self._json(404, {"error": "not found"})

    def _emit(self, value: object) -> None:
        self.wfile.write(
            f"data: {json.dumps(value, ensure_ascii=False)}\n\n".encode("utf-8")
        )
        self.wfile.flush()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}
        question = payload.get("question")
        image_received = _has_image(question)
        with _lock:
            _last.clear()
            _last.update({
                "path": self.path,
                "image_received": image_received,
                "question": question,
                "payload": payload,
            })

        answer = (
            "IMAGE_BLOCK_RECEIVED：已收到 canonical image block。"
            if image_received
            else "NO_IMAGE_BLOCK：未收到 canonical image block。"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self._emit({"event": "on_chat_model_start", "data": {}})
            for i in range(0, len(answer), 8):
                self._emit({
                    "event": "on_chat_model_stream",
                    "data": {"chunk": {"kwargs": {"content": answer[i:i + 8]}}},
                })
            self._emit({
                "event": "on_chat_model_end",
                "data": {"output": {"kwargs": {"usage_metadata": {
                    "input_tokens": 10,
                    "output_tokens": 8,
                    "total_tokens": 18,
                }}}},
            })
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8001), Handler).serve_forever()
