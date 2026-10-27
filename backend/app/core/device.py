"""本机统一内存预算：重负载任务启动前先做一次可用内存体检。

Apple Silicon 的 CPU 与 GPU 共享同一块统一内存。本机实测：视频生成峰值约 16-20 GiB，
嵌入模型微调约 10-15 GiB；两者同时跑会把系统压进 swap（swap 用满时机器不是变慢，
而是直接卡死）。32 GiB 机型上唯一可靠的策略是「串行 + 启动前体检」，
本模块负责体检这一半，调度交给调用方或 scripts/dev-stack.sh。

模块只用标准库，不依赖 pydantic 或第三方包，因此可以被脚本或测试单独导入。
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

GIB = 1024**3

# 经验峰值。视频生成取 MiniMax H3 的 32GB 档（Stream5 + Turbo4 + 4bit）实测上界；
# 微调取 2B 嵌入模型「bf16 冻结主干 + fp32 LoRA + 梯度检查点」的上界。
WORKLOAD_BUDGETS: dict[str, int] = {
    "video_generation": 20 * GIB,
    "embedding_finetune": 14 * GIB,
    "embedding_encode": 6 * GIB,
    "rag_stack": 6 * GIB,
}

WORKLOAD_LABELS: dict[str, str] = {
    "video_generation": "视频生成",
    "embedding_finetune": "嵌入模型微调",
    "embedding_encode": "嵌入模型编码",
    "rag_stack": "本地向量库栈",
}

# swap 已经用掉这么多时，连「可用内存看似够」也不能信：macOS 会把 inactive 页
# 报成可用，而它们其实已经在交换区里。这种情况直接拒绝启动重负载任务。
SWAP_HARD_LIMIT = 0.95

_VM_STAT_PAGE_SIZE = re.compile(r"page size of (\d+) bytes")
_VM_STAT_ENTRY = re.compile(r"^(.+?):\s+(\d+)\.?\s*$", re.MULTILINE)
_SWAP_TOTAL = re.compile(r"total = ([\d.]+)([BKMG])")
_SWAP_USED = re.compile(r"used = ([\d.]+)([BKMG])")
_SWAP_UNIT = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3}

MemoryReader = Callable[[], "MemorySnapshot"]


class DeviceMemoryError(RuntimeError):
    """可用内存不足以启动该任务。"""


@dataclass(frozen=True)
class MemorySnapshot:
    """一次内存体检的快照，单位统一为字节。"""

    total_bytes: int
    available_bytes: int
    swap_total_bytes: int
    swap_used_bytes: int

    @property
    def swap_pressure(self) -> float:
        if self.swap_total_bytes <= 0:
            return 0.0
        return self.swap_used_bytes / self.swap_total_bytes


def format_gib(value: int) -> str:
    return f"{value / GIB:.1f} GiB"


def _run(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=5, check=True
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"无法执行 {' '.join(command)}：{exc}") from exc
    return completed.stdout


def parse_vm_stat(text: str) -> tuple[int, dict[str, int]]:
    """解析 vm_stat 输出，返回 (页大小, {页名: 页数})。"""
    page_size = 4096
    matched_page_size = _VM_STAT_PAGE_SIZE.search(text)
    if matched_page_size:
        page_size = int(matched_page_size.group(1))
    counts: dict[str, int] = {}
    for name, value in _VM_STAT_ENTRY.findall(text):
        counts[name.strip()] = int(value)
    if not counts:
        raise RuntimeError("vm_stat 输出里没有可解析的页统计行")
    return page_size, counts


def parse_swapusage(text: str) -> tuple[int, int]:
    """解析 macOS sysctl -n vm.swapusage 输出，返回 (total_bytes, used_bytes)。"""
    total = _SWAP_TOTAL.search(text)
    used = _SWAP_USED.search(text)
    if not total or not used:
        raise RuntimeError(f"vm.swapusage 输出无法解析：{text!r}")
    total_bytes = int(float(total.group(1)) * _SWAP_UNIT[total.group(2)])
    used_bytes = int(float(used.group(1)) * _SWAP_UNIT[used.group(2)])
    return total_bytes, used_bytes


def _macos_snapshot() -> MemorySnapshot:
    total_bytes = int(_run(["sysctl", "-n", "hw.memsize"]).strip())
    page_size, counts = parse_vm_stat(_run(["vm_stat"]))
    # free + inactive + speculative 是 macOS 上「立刻能拿来用」的口径。
    reclaimable = (
        counts.get("Pages free", 0)
        + counts.get("Pages inactive", 0)
        + counts.get("Pages speculative", 0)
    )
    swap_total, swap_used = parse_swapusage(_run(["sysctl", "-n", "vm.swapusage"]))
    return MemorySnapshot(
        total_bytes=total_bytes,
        available_bytes=reclaimable * page_size,
        swap_total_bytes=swap_total,
        swap_used_bytes=swap_used,
    )


def parse_meminfo(text: str) -> dict[str, int]:
    """解析 Linux /proc/meminfo，返回 {键: 字节数}。"""
    fields: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            amount = int(parts[0])
        except ValueError:
            continue
        # meminfo 的单位写成 kB / MB，取首字母即可映射到 _SWAP_UNIT。
        unit = parts[1].upper()[:1] if len(parts) > 1 else ""
        fields[name.strip()] = amount * _SWAP_UNIT.get(unit, 1)
    if not fields:
        raise RuntimeError("/proc/meminfo 内容为空")
    return fields


def _linux_snapshot() -> MemorySnapshot:
    fields = parse_meminfo(Path("/proc/meminfo").read_text(encoding="utf-8"))
    swap_total = fields.get("SwapTotal", 0)
    swap_free = fields.get("SwapFree", 0)
    return MemorySnapshot(
        total_bytes=fields["MemTotal"],
        available_bytes=fields.get("MemAvailable", fields["MemFree"]),
        swap_total_bytes=swap_total,
        swap_used_bytes=max(swap_total - swap_free, 0),
    )


def read_memory() -> MemorySnapshot:
    """读取当前内存与 swap 快照。"""
    system = platform.system()
    if system == "Darwin":
        return _macos_snapshot()
    if system == "Linux":
        return _linux_snapshot()
    raise RuntimeError(f"暂不支持在 {system} 上做内存体检")


def describe(snapshot: MemorySnapshot) -> str:
    return (
        f"总内存 {format_gib(snapshot.total_bytes)}，"
        f"可用 {format_gib(snapshot.available_bytes)}，"
        f"swap {format_gib(snapshot.swap_used_bytes)}/"
        f"{format_gib(snapshot.swap_total_bytes)}"
        f"（{snapshot.swap_pressure:.0%}）"
    )


def require_headroom(
    workload: str, *, reader: MemoryReader = read_memory
) -> MemorySnapshot:
    """启动重负载任务前体检；不够就抛 DeviceMemoryError，不要等被 OOM 拖死。"""
    budget = WORKLOAD_BUDGETS.get(workload)
    if budget is None:
        raise ValueError(
            f"未知任务类型 {workload!r}，可选：{', '.join(sorted(WORKLOAD_BUDGETS))}"
        )
    label = WORKLOAD_LABELS.get(workload, workload)
    snapshot = reader()
    if snapshot.swap_pressure >= SWAP_HARD_LIMIT:
        raise DeviceMemoryError(
            f"swap 已用 {snapshot.swap_pressure:.0%}，系统正在拿硬盘当内存，"
            f"先关闭占内存的容器与应用再启动{label}。当前：{describe(snapshot)}"
        )
    if snapshot.available_bytes < budget:
        raise DeviceMemoryError(
            f"{label}需要约 {format_gib(budget)} 可用内存，"
            f"现在只有 {format_gib(snapshot.available_bytes)}。"
            f"当前：{describe(snapshot)}"
        )
    return snapshot


def report(reader: MemoryReader = read_memory) -> dict[str, object]:
    """生成一份可读的体检报告，供 CLI 与排障使用。"""
    snapshot = reader()
    workloads = {
        workload: {
            "label": WORKLOAD_LABELS.get(workload, workload),
            "budget_gib": round(budget / GIB, 1),
            "fits": snapshot.available_bytes >= budget
            and snapshot.swap_pressure < SWAP_HARD_LIMIT,
        }
        for workload, budget in WORKLOAD_BUDGETS.items()
    }
    return {
        "system": platform.system(),
        "total_gib": round(snapshot.total_bytes / GIB, 1),
        "available_gib": round(snapshot.available_bytes / GIB, 1),
        "swap_used_gib": round(snapshot.swap_used_bytes / GIB, 1),
        "swap_total_gib": round(snapshot.swap_total_bytes / GIB, 1),
        "swap_pressure": round(snapshot.swap_pressure, 3),
        "workloads": workloads,
    }


def main() -> int:
    result = report()
    print(json.dumps(result, ensure_ascii=False, indent=2))  # noqa: T201 - CLI 输出
    sys.stdout.flush()
    workloads = result["workloads"]
    assert isinstance(workloads, dict)
    blocked = [item["label"] for item in workloads.values() if not item["fits"]]
    if blocked:
        print(f"当前内存不允许启动：{'、'.join(blocked)}", file=sys.stderr)  # noqa: T201 - CLI 输出
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
