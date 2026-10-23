"""极简工具调用 Agent：让本地 Ollama 模型自己决定什么时候查库。

设计原则（也是这个文件刻意不做的事）：
- 只走本机回环 Ollama，不联网、不调用外部 API、不需要任何密钥。
- 单轮问答最多 AGENT_MAX_STEPS 步，超了强制收口，避免无限循环烧时间。
- 可调用的只有只读检索工具，没有写操作、没有下单、没有文件修改 —— 权限最小。
- 串行执行：一次只跑一个请求，不在内存里同时挂多个模型。
"""
import json

import httpx

from .config import (
    AGENT_DEFAULT_SESSION,
    AGENT_KEEP_TURNS,
    AGENT_MAX_STEPS,
    AGENT_MODEL,
    AGENT_NUM_CTX,
    AGENT_SUMMARY_MAX_CHARS,
    AGENT_SUMMARY_TIMEOUT_S,
    AGENT_TIMEOUT_S,
    OLLAMA_BASE_URL,
)
from .session import SessionStore
from .tools import TOOL_IMPL, TOOL_SPECS

SYSTEM_PROMPT = """你是「开心」，泽宝的本地资料库助手。

工作规则：
1. 只要问题涉及他的技术笔记、药品包装图、个人获奖证书、日语学习资料，
   就必须先调用 search_knowledge 查库，再根据查到的内容回答，不要凭空编造。
2. 范围选择：tech 是默认（技术笔记 + 药品图）；问日语用 japanese；问证书证件用 personal。
3. 最多查 2~3 次就够。如果查回来的内容回答不了问题，就直接说「库里没有相关内容」。
4. 回答用中文，简洁直接。引用到具体文件名时如实写出文件名，不要用相似度当确定度。
5. 多轮对话里，指代词（「它」「那个」「刚才那个」）优先理解成上一轮聊到的对象。"""


def _chat(messages: list[dict]) -> dict:
    """调一次本地 Ollama 的 /api/chat，返回 message 对象。"""
    payload = {
        "model": AGENT_MODEL,
        "messages": messages,
        "tools": TOOL_SPECS,
        "stream": False,
        "options": {"num_ctx": AGENT_NUM_CTX},
    }
    resp = httpx.post(
        f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=AGENT_TIMEOUT_S
    )
    resp.raise_for_status()
    return resp.json()["message"]


def _parse_args(raw) -> dict:
    """Ollama 的 arguments 可能是 dict（原生）也可能是 JSON 字符串（OpenAI 风格）。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


SUMMARY_PROMPT = """你是会话摘要助手。把对话压缩成要点，供之后继续对话时参考。

必须保留：
1. 用户问过哪些问题、关注什么；
2. 涉及的具体文件名、结论和数字；
3. 还没解决的问题。

禁止：
- 补充对话里没出现过的细节（笔画、写法、用法、评价、数据）；
- 编造结论、给建议、替用户表态。

只写对话里出现过的事实；不超过 {limit} 字。"""


def _summarize(previous: str, dropped: list[dict]) -> str:
    """把「旧摘要 + 即将被丢弃的轮次」压成新摘要。

    单独一次模型调用、单独超时预算、不带工具 —— 摘要本身要便宜且可控。
    失败时抛异常，由调用方决定怎么降级。
    """
    dialog = "\n".join(
        f"{'用户' if m['role'] == 'user' else '助手'}：{m['content']}" for m in dropped
    )
    user = f"【已有摘要】\n{previous}\n\n【新增对话】\n{dialog}" if previous else f"【新增对话】\n{dialog}"
    payload = {
        "model": AGENT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": SUMMARY_PROMPT.format(limit=AGENT_SUMMARY_MAX_CHARS),
            },
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"num_ctx": AGENT_NUM_CTX},
    }
    resp = httpx.post(
        f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=AGENT_SUMMARY_TIMEOUT_S
    )
    resp.raise_for_status()
    text = (resp.json().get("message", {}).get("content") or "").strip()
    if not text:
        raise ValueError("摘要返回为空")
    return text[:AGENT_SUMMARY_MAX_CHARS]


class ChatSession:
    """多轮会话：历史落盘 + 超长自动摘要压缩。

    - messages：system + 历次 user/assistant（逐字原文）。
    - summary：更早轮次压缩成的要点，放在 system 之后传给模型。
    - store：会话 JSON 文件，跨进程续聊靠它；退出不丢。

    两件刻意不做的事：
    1. 工具调用过程中的 tool 消息只在当轮内部循环里存在，不进历史 ——
       否则检索片段会一轮轮堆积撑爆 8192 上下文，模型还会把旧片段当新事实。
    2. 摘要不做成「无限累积」：长度有硬上限，太长就截断。
    """

    def __init__(
        self,
        verbose: bool = True,
        session_id: str = AGENT_DEFAULT_SESSION,
        persist: bool = True,
    ):
        self.verbose = verbose
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.summary = ""
        self.total_steps = 0
        self.summary_calls = 0
        self.turn_count = 0  # 累计轮数：压缩会丢消息，但计数不能回退
        self.resumed = False
        self.store = SessionStore(session_id) if persist else None

        if self.store:
            data = self.store.load()
            if data:
                self._restore(data)
                self.resumed = True

    def _restore(self, data: dict) -> None:
        """从磁盘恢复。只信任 role/content 两个字段，其余一律忽略。"""
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in data.get("messages", [])
            if isinstance(m, dict)
            and m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
        ]
        self.messages = [self.messages[0], *history]
        self.summary = str(data.get("summary") or "")
        self.total_steps = int(data.get("total_steps") or 0)
        self.summary_calls = int(data.get("summary_calls") or 0)
        # 累计轮数：老存档没有这个字段时，退回按历史里的助手消息数估算
        self.turn_count = int(data.get("turn_count") or 0) or sum(
            1 for m in history if m["role"] == "assistant"
        )

    @property
    def history(self) -> list[dict]:
        """不含 system 的历史消息。"""
        return self.messages[1:]

    @property
    def turns(self) -> int:
        """当前历史里还剩几轮（压缩后会变少，用于观察上下文占用）。"""
        return sum(1 for m in self.history if m["role"] == "assistant")

    def _request_messages(self, question: str) -> list[dict]:
        """发给模型的消息：system → 摘要（如有）→ 历史 → 本轮问题。"""
        msgs = [*self.messages]
        if self.summary:
            msgs.insert(
                1, {"role": "system", "content": f"【本会话前文摘要】\n{self.summary}"}
            )
        msgs.append({"role": "user", "content": question})
        return msgs

    def _compact(self) -> str | None:
        """超出保留窗口的旧轮次压成摘要。返回失败原因，None 表示成功。"""
        keep = AGENT_KEEP_TURNS * 2
        history = self.history
        if len(history) <= keep:
            return None

        dropped, kept = history[:-keep], history[-keep:]
        self.summary_calls += 1
        try:
            self.summary = _summarize(self.summary, dropped)
            self.messages = [self.messages[0], *kept]
            return None
        except Exception as exc:  # 摘要失败不能丢整个会话，降级为直接丢弃旧轮次
            self.messages = [self.messages[0], *kept]
            return f"{type(exc).__name__}: {exc}"

    def _save(self) -> None:
        if not self.store:
            return
        self.store.save(
            {
                "messages": self.history,
                "summary": self.summary,
                "total_steps": self.total_steps,
                "summary_calls": self.summary_calls,
                "turn_count": self.turn_count,
            }
        )

    def reset(self) -> None:
        """清空本会话（含磁盘文件）。"""
        self.messages = [self.messages[0]]
        self.summary = ""
        self.total_steps = 0
        self.summary_calls = 0
        self.turn_count = 0
        if self.store:
            self.store.delete()

    def _run_tool_loop(self, question: str) -> str:
        """本轮的工具调用循环。working 是「system + 摘要 + 历史 + 本轮」，只活在本轮。"""
        working: list[dict] = self._request_messages(question)

        for step in range(1, AGENT_MAX_STEPS + 1):
            msg = _chat(working)
            self.total_steps += 1
            calls = msg.get("tool_calls") or []

            if not calls:
                if self.verbose:
                    print(f"[agent] 第 {step} 步：模型直接作答")
                return (msg.get("content") or "").strip()

            working.append(msg)  # 必须把带 tool_calls 的助手消息回填给模型
            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                args = _parse_args(fn.get("arguments"))
                if self.verbose:
                    print(f"[agent] 第 {step} 步：调用 {name}({args})")

                impl = TOOL_IMPL.get(name)
                result = impl(**args) if impl else f"未知工具：{name}"
                if self.verbose:
                    print("        " + result.replace("\n", "\n        "))
                working.append({"role": "tool", "name": name, "content": result})

        return "（已达到最大步数上限，未能得出结论。换个更具体的问法再试一次。）"

    def turn(self, question: str) -> str:
        """问一轮：跑完工具循环 → 入历史 → 按需压缩 → 落盘。"""
        answer = self._run_tool_loop(question)
        self.messages.append({"role": "user", "content": question})
        self.messages.append({"role": "assistant", "content": answer})
        failed = self._compact()
        self.turn_count += 1  # 累计轮数；压缩会丢消息，但「第几轮」不能因此回退
        self._save()

        if self.verbose:
            msg = (
                f"[session] 第 {self.turn_count} 轮（历史剩 {self.turns} 轮）"
                f"｜保留原文 {len(self.history)} 条"
                f"｜摘要 {len(self.summary)} 字｜累计模型调用 {self.total_steps}"
            )
            if self.summary_calls:
                msg += f"（含摘要 {self.summary_calls} 次）"
            print(msg)
            if failed:
                print(f"[session] ⚠️ 摘要失败，已降级为丢弃旧轮次：{failed}")
        return answer


def ask(question: str, verbose: bool = True) -> str:
    """单轮问答（不落盘、无历史记忆）。适合一次性提问。"""
    return ChatSession(verbose=verbose, persist=False).turn(question)
