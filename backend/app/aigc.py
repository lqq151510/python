"""本地 AIGC 视频生成：参数校验、ComfyUI 工作流构造与 HTTP 往返。

链路：前端表单 -> devassist API（落库即返回 job_id）-> 单并发执行器
-> ComfyUI POST /prompt -> 轮询 /history -> GET /view 取回 mp4 -> 写回任务记录。

本模块刻意不碰数据库、不起线程，只负责「参数 -> 工作流 -> ComfyUI 往返」这一段，
因此可以用假 ComfyUI 服务做单元测试；排队与状态落库在 app.aigc_runner。

两个来自节点源码的硬约束（不要放宽）：
1. width / height 必须是 32 的倍数，否则节点直接 raise ValueError；
2. 同一条生成任务在本机是分钟级的（5 秒 864x480 约 12 分钟），接口必须异步。
"""

from __future__ import annotations

import json
import random
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.core.config import settings

AigcKind = Literal["text2video", "image2video"]
AigcStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]

KIND_TEXT2VIDEO: AigcKind = "text2video"
KIND_IMAGE2VIDEO: AigcKind = "image2video"
AIGC_KINDS: tuple[AigcKind, ...] = (KIND_TEXT2VIDEO, KIND_IMAGE2VIDEO)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELED = "canceled"
TERMINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELED})

# 与 custom_nodes/ComfyUI-MiniMax-H3-MLX-SolAttn/comfyui_nodes.py 的 INPUT_TYPES 对齐
MODEL_PROFILES = (
    "4-bit",
    "8-bit",
    "bf16",
    "bf16-pruned",
    "4-bit-pruned",
    "8-bit-pruned",
    "attention16-mlp8-pruned",
)
GENERATION_PROFILES = ("Turbo 4 Fast", "Turbo 8 Balanced", "Full 20 Quality")
MEMORY_MODES = ("auto", "resident", "stream2", "stream5")
QWEN_PRECISIONS = ("prequantized 8-bit",)
ATTENTION_MODES = ("sol_attn", "dense")

SIDE_STEP = 32
MIN_SIDE = 256
MAX_WIDTH = 1280
MAX_HEIGHT = 720
MIN_DURATION = 5.0
MAX_DURATION = 15.0
MAX_SEED = 2**64 - 1

# 节点自带 MarkdownNote 里的实测公式（5 秒 / Turbo 4 / M 系芯片）：
# T ≈ 16 + 0.00173 × 宽 × 高 秒。只用来给用户一个预估进度，不参与调度。
ESTIMATE_BASE_SECONDS = 16.0
ESTIMATE_PIXEL_SECONDS = 0.00173

VIDEO_SUFFIXES = (".mp4", ".webm", ".mkv", ".mov")
IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
MAX_ERROR_CHARS = 480


class AigcError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(code)


def _clip_error(message: object) -> str:
    text = " ".join(str(message).split())
    if len(text) > MAX_ERROR_CHARS:
        return text[: MAX_ERROR_CHARS - 1] + "…"
    return text


class VideoParams(BaseModel):
    """一次生成任务的 H3 参数。默认值对应本机 32 GiB 的草稿档。"""

    model_config = ConfigDict(extra="forbid")

    width: int = Field(default=480, ge=MIN_SIDE, le=MAX_WIDTH)
    height: int = Field(default=288, ge=MIN_SIDE, le=MAX_HEIGHT)
    duration_seconds: float = Field(default=5.0, ge=MIN_DURATION, le=MAX_DURATION)
    seed: int = Field(
        default_factory=lambda: random.randint(0, MAX_SEED), ge=0, le=MAX_SEED
    )
    model_profile: Literal[
        "4-bit",
        "8-bit",
        "bf16",
        "bf16-pruned",
        "4-bit-pruned",
        "8-bit-pruned",
        "attention16-mlp8-pruned",
    ] = "4-bit"
    generation_profile: Literal[
        "Turbo 4 Fast", "Turbo 8 Balanced", "Full 20 Quality"
    ] = "Turbo 4 Fast"
    memory_mode: Literal["auto", "resident", "stream2", "stream5"] = "stream5"
    qwen_precision: Literal["prequantized 8-bit"] = "prequantized 8-bit"
    attention: Literal["sol_attn", "dense"] = "sol_attn"
    sol_tau: float = Field(default=1.3, ge=0.0, le=4.0)

    @field_validator("width", "height")
    @classmethod
    def _multiple_of_step(cls, value: int, info: ValidationInfo) -> int:
        if value % SIDE_STEP:
            raise ValueError(
                f"{info.field_name} 必须是 {SIDE_STEP} 的倍数，收到 {value}"
            )
        return value

    @field_validator("duration_seconds")
    @classmethod
    def _round_duration(cls, value: float) -> float:
        return round(value, 1)

    @property
    def estimate_seconds(self) -> float:
        return ESTIMATE_BASE_SECONDS + ESTIMATE_PIXEL_SECONDS * self.width * self.height

    def to_json(self) -> str:
        return json.dumps(self.model_dump(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> VideoParams:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise AigcError(500, "aigc_params_corrupt", "任务参数已损坏") from exc
        if not isinstance(payload, dict):
            raise AigcError(500, "aigc_params_corrupt", "任务参数已损坏")
        return cls.model_validate(payload)


class AigcJobCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    kind: Literal["text2video", "image2video"] = KIND_TEXT2VIDEO
    prompt: str = Field(min_length=1, max_length=2000)
    params: VideoParams = Field(default_factory=VideoParams)

    @field_validator("prompt")
    @classmethod
    def _prompt_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("提示词不能为空")
        return value.strip()


def estimate_progress(elapsed_seconds: float, estimate_seconds: float) -> int:
    """把已耗时映射成 0-95 的预估进度；真正完成由 ComfyUI 的历史记录判定。"""
    if estimate_seconds <= 0:
        return 1
    ratio = elapsed_seconds / estimate_seconds
    return max(1, min(95, int(ratio * 90) + 5))


def build_prompt(
    *,
    prompt: str,
    params: VideoParams,
    filename_prefix: str,
    image_name: str | None = None,
) -> dict[str, dict[str, Any]]:
    """构造 API 格式工作流（就是 POST /prompt 要的 prompt 字段）。

    UI 格式（nodes/links）不能直接提交，这里按节点源码的输入签名手写固定图：
    1 MiniMaxH3MLXTurbo -> 2 CreateVideo -> 3 SaveVideo，图生视频再加 4 LoadImage。
    SaveAudio 故意不加：音轨已经由 CreateVideo 混进视频里。
    """
    if not prompt.strip():
        raise AigcError(400, "aigc_prompt_empty", "提示词不能为空")

    generator_inputs: dict[str, Any] = {
        "prompt": prompt,
        "model_profile": params.model_profile,
        "generation_profile": params.generation_profile,
        "memory_mode": params.memory_mode,
        "qwen_precision": params.qwen_precision,
        "attention": params.attention,
        "width": params.width,
        "height": params.height,
        "duration_seconds": params.duration_seconds,
        "seed": params.seed,
        "sol_tau": params.sol_tau,
    }
    graph: dict[str, dict[str, Any]] = {
        "1": {"class_type": "MiniMaxH3MLXTurbo", "inputs": generator_inputs},
        "2": {
            "class_type": "CreateVideo",
            "inputs": {"images": ["1", 0], "audio": ["1", 1], "fps": 24.0},
        },
        "3": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["2", 0],
                "filename_prefix": filename_prefix,
                "format": "mp4",
                "codec": "auto",
            },
        },
    }
    if image_name:
        graph["4"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
        generator_inputs["first_frame"] = ["4", 0]
    return graph


def history_entry(history: dict[str, Any], prompt_id: str) -> dict[str, Any] | None:
    entry = history.get(prompt_id)
    return entry if isinstance(entry, dict) else None


def history_status(entry: dict[str, Any]) -> str:
    status = entry.get("status")
    if isinstance(status, dict):
        status_str = status.get("status_str")
        if isinstance(status_str, str) and status_str in ("success", "error"):
            return status_str
        if status.get("completed"):
            return "success"
    # 老版本 ComfyUI 没有 status 字段，能查到 outputs 就算完成
    if entry.get("outputs"):
        return "success"
    return "pending"


def history_error(entry: dict[str, Any]) -> str:
    status = entry.get("status")
    messages = status.get("messages") if isinstance(status, dict) else None
    if isinstance(messages, list):
        for item in messages:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            event, payload = item[0], item[1]
            if event != "execution_error" or not isinstance(payload, dict):
                continue
            node = payload.get("node_type") or payload.get("node_id") or "未知节点"
            detail = payload.get("exception_message") or payload.get("exception_type")
            return _clip_error(f"{node}: {detail}")
    return "ComfyUI 执行失败，未返回具体错误"


def saved_media(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """从 history 的 outputs 里捞出所有已保存的媒体文件描述。

    ComfyUI 不同版本的保存节点把结果放在 images / gifs / videos 等不同键里，
    所以这里不按节点名匹配，只认「带 filename 的字典」再按后缀筛视频。
    """
    found: list[dict[str, Any]] = []
    outputs = entry.get("outputs")
    if not isinstance(outputs, dict):
        return found
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        for value in node_output.values():
            if not isinstance(value, list):
                continue
            for item in value:
                if isinstance(item, dict) and isinstance(item.get("filename"), str):
                    found.append(item)
    return found


def saved_videos(entry: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in saved_media(entry)
        if item["filename"].lower().endswith(VIDEO_SUFFIXES)
    ]


class ComfyClient:
    """ComfyUI 的最小 HTTP 客户端。只连本机回环地址，显式绕过系统代理。"""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or str(settings.AIGC_COMFY_URL)).rstrip("/")
        self.timeout = timeout or settings.AIGC_HTTP_TIMEOUT_SECONDS
        self._client = client

    def _http(self) -> httpx.Client:
        if self._client is None:
            # trust_env=False：本机 http_proxy 指向 127.0.0.1:7896，会让回环请求也被劫持。
            self._client = httpx.Client(
                timeout=self.timeout, trust_env=False, follow_redirects=False
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def system_stats(self) -> dict[str, Any]:
        try:
            response = self._http().get(f"{self.base_url}/system_stats")
        except httpx.HTTPError as exc:
            raise AigcError(
                503,
                "aigc_comfy_unreachable",
                f"连不上 ComfyUI（{self.base_url}）：{_clip_error(exc)}",
            ) from exc
        if response.status_code >= 400:
            raise AigcError(
                503,
                "aigc_comfy_unreachable",
                f"ComfyUI 返回 HTTP {response.status_code}",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise AigcError(
                502, "aigc_comfy_bad_response", "ComfyUI 返回的不是 JSON"
            ) from exc
        return data if isinstance(data, dict) else {}

    def submit(self, graph: dict[str, dict[str, Any]]) -> str:
        payload = {"prompt": graph, "client_id": uuid.uuid4().hex}
        try:
            response = self._http().post(f"{self.base_url}/prompt", json=payload)
        except httpx.HTTPError as exc:
            raise AigcError(
                503,
                "aigc_comfy_unreachable",
                f"提交任务到 ComfyUI 失败：{_clip_error(exc)}",
            ) from exc
        if response.status_code >= 400:
            raise AigcError(
                502,
                "aigc_comfy_rejected",
                f"ComfyUI 拒绝了工作流（HTTP {response.status_code}）："
                f"{_clip_error(response.text)}",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise AigcError(
                502, "aigc_comfy_bad_response", "ComfyUI 返回的不是 JSON"
            ) from exc
        prompt_id = data.get("prompt_id") if isinstance(data, dict) else None
        if not isinstance(prompt_id, str) or not prompt_id:
            raise AigcError(
                502,
                "aigc_comfy_rejected",
                f"ComfyUI 没有返回 prompt_id：{_clip_error(data)}",
            )
        return prompt_id

    def upload_image(self, *, filename: str, content: bytes, content_type: str) -> str:
        """把首帧图上传到 ComfyUI 的 input 目录，返回 LoadImage 可用的名字。"""
        files = {"image": (filename, content, content_type)}
        data = {"overwrite": "true", "type": "input"}
        try:
            response = self._http().post(
                f"{self.base_url}/upload/image", files=files, data=data
            )
        except httpx.HTTPError as exc:
            raise AigcError(
                503,
                "aigc_comfy_unreachable",
                f"上传首帧图失败：{_clip_error(exc)}",
            ) from exc
        if response.status_code >= 400:
            raise AigcError(
                502,
                "aigc_comfy_rejected",
                f"ComfyUI 拒绝上传首帧图（HTTP {response.status_code}）",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise AigcError(
                502, "aigc_comfy_bad_response", "上传接口返回的不是 JSON"
            ) from exc
        name = payload.get("name") if isinstance(payload, dict) else None
        subfolder = payload.get("subfolder") if isinstance(payload, dict) else None
        if not isinstance(name, str) or not name:
            raise AigcError(502, "aigc_comfy_bad_response", "上传接口没有返回文件名")
        return f"{subfolder}/{name}" if subfolder else name

    def history(self, prompt_id: str) -> dict[str, Any]:
        try:
            response = self._http().get(f"{self.base_url}/history/{prompt_id}")
        except httpx.HTTPError as exc:
            raise AigcError(
                503, "aigc_comfy_unreachable", f"查询任务状态失败：{_clip_error(exc)}"
            ) from exc
        if response.status_code >= 400:
            raise AigcError(
                502,
                "aigc_comfy_bad_response",
                f"查询任务状态返回 HTTP {response.status_code}",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise AigcError(
                502, "aigc_comfy_bad_response", "状态接口返回的不是 JSON"
            ) from exc
        return data if isinstance(data, dict) else {}

    def queue(self) -> dict[str, Any]:
        try:
            response = self._http().get(f"{self.base_url}/queue")
        except httpx.HTTPError as exc:
            raise AigcError(
                503, "aigc_comfy_unreachable", f"查询队列失败：{_clip_error(exc)}"
            ) from exc
        if response.status_code >= 400:
            return {}
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def interrupt(self) -> bool:
        try:
            response = self._http().post(f"{self.base_url}/interrupt", json={})
        except httpx.HTTPError:
            return False
        return response.status_code < 400

    def download(
        self,
        *,
        filename: str,
        subfolder: str,
        folder_type: str,
        destination: Path,
    ) -> int:
        """把 /view 的产物流式写盘，返回字节数。"""
        params = {
            "filename": filename,
            "subfolder": subfolder,
            "type": folder_type or "output",
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with self._http().stream(
                "GET", f"{self.base_url}/view", params=params
            ) as response:
                if response.status_code >= 400:
                    raise AigcError(
                        502,
                        "aigc_output_missing",
                        f"取回产物失败（HTTP {response.status_code}）",
                    )
                tmp = destination.with_suffix(destination.suffix + ".part")
                with tmp.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
                        written += len(chunk)
                tmp.replace(destination)
        except httpx.HTTPError as exc:
            raise AigcError(
                503, "aigc_comfy_unreachable", f"下载产物失败：{_clip_error(exc)}"
            ) from exc
        if written == 0:
            destination.unlink(missing_ok=True)
            raise AigcError(502, "aigc_output_empty", "取回的产物是空文件")
        return written


def output_filename(prompt_id: str) -> str:
    """给下载到本机的产物起个稳定文件名。"""
    suffix = int(time.time() * 1000) % 100_000_000
    return f"{prompt_id[:8]}-{suffix:08d}.mp4"
