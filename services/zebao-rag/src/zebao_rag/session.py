"""会话落盘：把多轮对话存成本地 JSON，支持跨进程续聊。

设计要点：
- 一个会话一个文件：data/sessions/<会话名>.json，人类可读、可直接删除。
- 会话名做白名单校验，禁止路径分隔符与 .. ，避免路径穿越写到目录外。
- 写入用「临时文件 + 原子替换」，避免写到一半崩溃把会话记录写坏。
- 只读本地磁盘，不联网、不上传。
"""
import json
import re
import time
from pathlib import Path

from .config import AGENT_DEFAULT_SESSION, SESSIONS_DIR

# 会话名白名单：中文、字母、数字、下划线、连字符
_NAME_OK = re.compile(r"^[\w\u4e00-\u9fff-]{1,40}$")


def safe_session_name(name: str) -> str:
    """校验并返回安全会话名；不合法就退化为默认名，绝不拼进路径。"""
    name = (name or "").strip()
    if not name:
        return AGENT_DEFAULT_SESSION
    return name if _NAME_OK.match(name) else AGENT_DEFAULT_SESSION


class SessionStore:
    """单个会话文件的读写。"""

    def __init__(self, name: str = AGENT_DEFAULT_SESSION):
        self.name = safe_session_name(name)
        self.path: Path = SESSIONS_DIR / f"{self.name}.json"

    @staticmethod
    def list_sessions() -> list[dict]:
        """列出已有会话（按更新时间倒序）。"""
        if not SESSIONS_DIR.exists():
            return []
        out: list[dict] = []
        for p in SESSIONS_DIR.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                out.append({"name": p.stem, "turns": 0, "summary": "", "updated_at": ""})
                continue
            # 优先用累计轮数（压缩会丢历史消息，按消息数算会少报）；旧文件无此字段则回退
            turns = int(data.get("turn_count") or 0)
            if not turns:
                turns = sum(
                    1 for m in data.get("messages", []) if m.get("role") == "assistant"
                )
            out.append(
                {
                    "name": p.stem,
                    "turns": turns,
                    "summary": data.get("summary", ""),
                    "updated_at": data.get("updated_at", ""),
                }
            )
        out.sort(key=lambda d: d["updated_at"], reverse=True)
        return out

    def load(self) -> dict | None:
        """读会话；文件不存在或损坏都返回 None，由调用方决定是否新建。"""
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def save(self, data: dict) -> Path:
        data = {**data, "name": self.name, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)  # 原子替换
        return self.path

    def delete(self) -> bool:
        if self.path.exists():
            self.path.unlink()
            return True
        return False
