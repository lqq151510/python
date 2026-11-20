"""Lock the literal-evidence rules without reading holdout failures back into them."""

from pathlib import Path

import pytest

from app.evidence_gap import missing_required_literal
from app.retrieval_eval import evaluate_case, load_dataset

_EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.mark.parametrize(
    ("question", "chunks", "expected"),
    [
        ("请给出验证命令。", ["只说明了缓存会过期。"], "missing_command"),
        ("请给出验证命令。", ["运行 `redis-cli ping`。"], None),
        ("执行哪条热更新命令？", ["alembic merge heads"], None),
        ("应该修改哪个环境变量？", ["把超时写在说明里。"], "missing_config_key"),
        ("应该修改哪个环境变量？", ["使用 DEEPSEEK_API_KEY。"], None),
        (
            "明文凭证能塞进网页环境变量里吗",
            ["前端不得使用 VITE_ 前缀保存模型密钥。"],
            None,
        ),
        (
            "项目从哪个配置文件读取密钥？",
            ["密钥留空时拒绝启动。"],
            "missing_config_file",
        ),
        ("项目从哪个配置文件读取密钥？", ["读取 config.toml。"], None),
        ("项目从哪个配置文件读取密钥？", ["接口写在 /api 说明中。"], None),
        ("给出本地后端绑定的完整 URL。", ["服务只说明了鉴权。"], "missing_url"),
        ("刷新令牌接口的 URL 是什么？", ["过期时返回 401。"], "missing_url"),
        (
            "给出本地后端绑定的完整 URL。",
            ["打开 http://127.0.0.1:8000。"],
            None,
        ),
        ("初始等待毫秒数和抖动范围是多少？", ["总预算为 30 秒。"], "missing_term"),
        ("初始等待毫秒数是多少？", ["初始等待 200 毫秒。"], None),
        ("异步请求会阻塞吗？", ["等待时会把控制权交回事件循环。"], None),
        (
            "请给出 Redis 的验证命令。",
            ["状态检查使用 GET /healthz。", "Redis 只说明了过期时间。"],
            "missing_command",
        ),
        (
            "请给出 Redis 的验证命令。",
            ["状态检查使用 GET /healthz。", "运行 `redis-cli ping`。"],
            None,
        ),
        (
            "pool_wait 之后应该修改哪个环境变量？",
            ["使用 MAX_CONNECTIONS。"],
            "missing_config_key",
        ),
        (
            "pool_wait 之后应该修改哪个环境变量？",
            ["pool_wait 对应 POOL_LIMIT。"],
            None,
        ),
    ],
)
def test_literal_gap_rules(
    question: str, chunks: list[str], expected: str | None
) -> None:
    assert missing_required_literal(question, chunks) == expected


def test_command_beyond_the_top_five_chunks_does_not_count() -> None:
    chunks = ["无关说明。"] * 5 + ["运行 `redis-cli ping`。"]
    assert missing_required_literal("请给出验证命令。", chunks) == "missing_command"


def test_answerable_eval_cases_are_not_refused() -> None:
    refused: list[str] = []
    for name in ("retrieval-eval.json", "retrieval-eval-holdout.json"):
        dataset, chunks, _hashes = load_dataset(_EXAMPLES, _EXAMPLES / name)
        for case in dataset.cases:
            if not case.expected:
                continue
            result = evaluate_case(case, chunks)
            reason = missing_required_literal(
                case.query,
                [
                    f"{source.heading}\n{source.content}"
                    for source in result.sources
                ],
            )
            if reason is not None:
                refused.append(f"{name}:{case.id}:{reason}")
    assert refused == []
