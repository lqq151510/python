"""假的 ComfyUI 服务，只用于本地联调 devassist 的 AIGC 链路。

它不是模拟推理，只按 ComfyUI 的 HTTP 契约回应：
  GET  /system_stats          设备信息
  POST /prompt                接受工作流，返回 prompt_id
  GET  /history/{prompt_id}   第一次返回空（排队中），之后返回成功产物
  GET  /view                  返回一小段假 mp4 字节
  POST /upload/image          返回上传后的文件名
  POST /interrupt             中断

用法：python3 backend/scripts/fake-comfyui.py --port 8189
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import uuid
from collections.abc import Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

POLLS_BEFORE_READY = int(os.environ.get("FAKE_COMFY_POLLS_BEFORE_READY", "1"))
# 提示词里带这个标记的任务永远等不到成功，用来验证取消流程。
SLOW_MARKER = "慢一点"
SLOW_POLLS = 100_000


class FakeState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self.interrupts = 0
        self.uploads: list[str] = []
        self.graphs: list[dict[str, Any]] = []

    def submit(self, graph: dict[str, Any]) -> str:
        prompt_id = uuid.uuid4().hex
        prompt = str(graph.get("1", {}).get("inputs", {}).get("prompt", ""))
        ready_after = SLOW_POLLS if SLOW_MARKER in prompt else POLLS_BEFORE_READY
        with self._lock:
            self._jobs[prompt_id] = {
                "created": time.monotonic(),
                "polls": 0,
                "graph": graph,
                "ready_after": ready_after,
            }
            self.graphs.append(graph)
        return prompt_id

    def poll(self, prompt_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(prompt_id)
            if job is None:
                return None
            job["polls"] += 1
            ready = job["polls"] > job["ready_after"]
        if not ready:
            return {}
        return {
            "prompt": [0, prompt_id, {}, {}, []],
            "outputs": {
                "3": {
                    "videos": [
                        {
                            "filename": f"fake-{prompt_id[:8]}.mp4",
                            "subfolder": "aigc",
                            "type": "output",
                        }
                    ]
                }
            },
            "status": {"status_str": "success", "completed": True, "messages": []},
        }

    def record_upload(self, name: str) -> None:
        with self._lock:
            self.uploads.append(name)

    def record_interrupt(self) -> None:
        with self._lock:
            self.interrupts += 1


STATE = FakeState()
BODY = b"\x00\x00\x00\x18ftypmp42" + b"fake-comfyui-artifact"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - BaseHTTPRequestHandler API
        pass

    def _send(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        url = urlparse(self.path)
        if url.path == "/system_stats":
            self._json(
                {
                    "system": {"os": "fake", "comfyui_version": "0.0.0-fake"},
                    "devices": [{"name": "fake", "type": "fake", "vram_total": 0}],
                }
            )
            return
        if url.path.startswith("/history/"):
            prompt_id = url.path.rsplit("/", 1)[-1]
            entry = STATE.poll(prompt_id)
            if entry is None:
                self._json({})
            else:
                self._json({prompt_id: entry})
            return
        if url.path == "/view":
            query = parse_qs(url.query)
            filename = (query.get("filename") or [""])[0]
            self._send(200, BODY + filename.encode(), "video/mp4")
            return
        if url.path == "/queue":
            self._json({"queue_running": [], "queue_pending": []})
            return
        if url.path == "/debug/state":
            with STATE._lock:
                self._json(
                    {
                        "uploads": list(STATE.uploads),
                        "interrupts": STATE.interrupts,
                        "graphs": len(STATE.graphs),
                    }
                )
            return
        self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        url = urlparse(self.path)
        if url.path == "/prompt":
            graph = self._read_json().get("prompt")
            if not isinstance(graph, dict):
                self._json({"error": "missing prompt"}, status=400)
                return
            prompt_id = STATE.submit(graph)
            self._json({"prompt_id": prompt_id, "number": 1, "node_errors": {}})
            return
        if url.path == "/upload/image":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            name = f"upload-{uuid.uuid4().hex[:8]}.png"
            STATE.record_upload(name)
            self._json({"name": name, "subfolder": "", "type": "input", "size": len(raw)})
            return
        if url.path == "/interrupt":
            STATE.record_interrupt()
            self._send(200, b"{}", "application/json")
            return
        self._json({"error": "not found"}, status=404)


def serve(port: int) -> Iterable[str]:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"fake comfyui listening on http://127.0.0.1:{port}", flush=True)  # noqa: T201
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8189)
    args = parser.parse_args()
    serve(args.port)


if __name__ == "__main__":
    main()
